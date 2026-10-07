"""Optional model process. JSONL stdout contains protocol only, never input text."""
import argparse
import json
import math
import os
import sys
from time import perf_counter

from .inputs import make_windows, PAIR_LIMIT

QWEN_PREFIX = ('<|im_start|>system\nJudge whether the Document meets the requirements based on '
               'the Query and the Instruct provided. Note that the answer can only be '
               '"yes" or "no".<|im_end|>\n<|im_start|>user\n')
QWEN_SUFFIX = '<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n'
INSTRUCTION = 'Given a web search query, retrieve relevant passages that answer the query'


def qwen_input(query: str, document: str) -> str:
    return (QWEN_PREFIX + '<Instruct>: ' + INSTRUCTION + '\n<Query>: ' + query +
            '\n<Document>: ' + document + QWEN_SUFFIX)


def yes_no_probability(yes: float, no: float) -> float:
    difference = yes - no
    return 1. / (1. + math.exp(-difference)) if difference >= 0 else (
        math.exp(difference) / (1. + math.exp(difference)))


def score_request(request: dict, *, tokenizer, score_pairs) -> dict:
    started = perf_counter()
    if not isinstance(request.get('query'), str) or type(request.get('top_n')) is not int:
        raise ValueError('invalid request')
    prefix = request['blocks'][:request['top_n']]
    if [b['index'] for b in prefix] != list(range(len(prefix))):
        raise ValueError('invalid indexes')
    pairs, counts = [], []
    for block in prefix:
        windows = make_windows(request['query'], block, tokenizer=tokenizer,
                               forms=tuple(block.get('forms', ())))
        counts.append(len(windows))
        pairs.extend((request['query'], block.get('title', '') + '\n' + w) for w in windows)
    scores = score_pairs(pairs) if pairs else []
    if len(scores) != len(pairs) or any(not math.isfinite(v) for v in scores):
        raise ValueError('invalid scores')
    position, aggregated = 0, []
    for i, count in enumerate(counts):
        aggregated.append({'index': i, 'score': max(scores[position:position + count])})
        position += count
    return {'request_id': request['request_id'], 'status': 'applied', 'scores': aggregated,
            'window_counts': counts, 'truncated_blocks': sum(c > 1 for c in counts),
            'elapsed_ms': (perf_counter() - started) * 1000}


class ModelScorer:
    def __init__(self, model: str, revision: str, cache: str, device: str, batch_size: int):
        # Some tokenizer metadata paths contact Hub despite local_files_only.
        # The scoring process must never download or query remote metadata.
        os.environ['HF_HUB_OFFLINE'] = '1'
        os.environ['TRANSFORMERS_OFFLINE'] = '1'
        import torch
        import transformers
        from transformers import (AutoTokenizer, AutoModelForCausalLM,
                                  AutoModelForSequenceClassification)
        self.torch, self.device, self.batch_size = torch, device, batch_size
        torch.set_num_threads(4)
        if device == 'cuda':
            if not torch.cuda.is_available():
                raise RuntimeError('cuda unavailable')
            torch.cuda.set_per_process_memory_fraction(.375)
        self.qwen = model.startswith('Qwen/')
        options = dict(revision=revision, cache_dir=cache, local_files_only=True,
                       trust_remote_code=False)
        self.tokenizer = AutoTokenizer.from_pretrained(model, padding_side='left' if self.qwen
                                                       else 'right', **options)
        cls = AutoModelForCausalLM if self.qwen else AutoModelForSequenceClassification
        self.model = cls.from_pretrained(model, torch_dtype=torch.float16 if device == 'cuda'
                                         else torch.float32, **options).to(device).eval()
        if self.qwen:
            self.yes = self.tokenizer.convert_tokens_to_ids('yes')
            self.no = self.tokenizer.convert_tokens_to_ids('no')
        self.metadata = {'model': model, 'revision': revision, 'tokenizer_revision': revision,
                         'device': device, 'batch_size': batch_size, 'torch': torch.__version__,
                         'transformers': transformers.__version__, 'cpu_threads': 4,
                         'pair_limit': PAIR_LIMIT, 'window_limit': 1024, 'overlap': 128,
                         'max_windows': 4, 'window_aggregation': 'max',
                         'precision': 'float16' if device == 'cuda' else 'float32'}
        if device == 'cuda':
            properties = torch.cuda.get_device_properties(0)
            self.metadata.update(gpu=properties.name, gpu_total_bytes=properties.total_memory,
                                 gpu_memory_fraction=.375,
                                 attention_implementation=self.model.config._attn_implementation)

    def score(self, pairs):
        result = []
        for start in range(0, len(pairs), self.batch_size):
            batch = pairs[start:start + self.batch_size]
            if self.qwen:
                inputs = self.tokenizer([qwen_input(q, d) for q, d in batch], padding=True,
                                        truncation=False, return_tensors='pt')
            else:
                inputs = self.tokenizer([q for q, _ in batch], [d for _, d in batch],
                                        padding=True, truncation=False, return_tensors='pt')
            if inputs['input_ids'].shape[1] > PAIR_LIMIT:
                raise ValueError('unsupported input pair')
            inputs = inputs.to(self.device)
            with self.torch.inference_mode():
                if self.qwen:
                    logits = self.model(**inputs, logits_to_keep=1).logits[:, -1, :]
                    probabilities = self.torch.softmax(logits[:, [self.no, self.yes]].float(), dim=1)
                    result.extend(probabilities[:, 1].cpu().tolist())
                else:
                    result.extend(self.model(**inputs).logits.reshape(-1).float().cpu().tolist())
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--cache', required=True)
    parser.add_argument('--device', choices=['cpu', 'cuda'], default='cpu')
    parser.add_argument('--batch-size', type=int, default=8)
    args = parser.parse_args()
    try:
        scorer = ModelScorer(args.model, args.revision, args.cache, args.device, args.batch_size)
    except Exception:
        print(json.dumps({'status': 'unavailable'}), flush=True)
        return 1
    print(json.dumps({'status': 'ready', **scorer.metadata}), flush=True)
    while True:
        line = sys.stdin.buffer.readline(16 * 1024 * 1024 + 1)
        if not line:
            break
        try:
            if len(line) > 16 * 1024 * 1024:
                raise ValueError('request too large')
            request = json.loads(line)
            response = score_request(request, tokenizer=scorer.tokenizer, score_pairs=scorer.score)
            if scorer.device == 'cuda':
                response['peak_gpu_bytes'] = scorer.torch.cuda.max_memory_allocated()
        except Exception as exc:
            status = 'unsupported_input' if isinstance(exc, ValueError) and 'input' in str(exc) else (
                'invalid_response')
            response = {'request_id': request.get('request_id') if isinstance(
                locals().get('request'), dict) else None, 'status': status}
        print(json.dumps(response), flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
