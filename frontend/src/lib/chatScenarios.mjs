export function scenarioDraft(source) {
  return {name:source?.name ?? "",description:source?.description ?? "",instructions:source?.instructions ?? "",visibility:source?.visibility ?? "private"};
}
export function validateScenarioDraft(draft) {
  const name=draft.name.trim(),description=draft.description.trim(),instructions=draft.instructions.trim();
  return name.length>0 && name.length<=120 && description.length<=1000 && instructions.length>0 && instructions.length<=12000;
}
export function scenarioNetworkOptions(options) {
  return options.responseMode==="scenario" ? {scenarioId:options.requestScenarioId} : {};
}
export function previewSearch(options,query,locale) {
  return {query,locale,tags:options.requestTags ?? [],top_k:options.requestTopK ?? 5,
    mode:options.requestMode,search_depth:options.requestSearchDepth,search_doc_ids:options.requestDocIds,
    mail_mode:options.requestMailMode ?? "all",use_glossary:options.requestUseGlossary ?? true,
    assess_sources:options.requestAssessSources,source_locales:options.requestSourceLocale && options.requestSourceLocale!=="unknown" ? [options.requestSourceLocale] : [],
    include_unknown_source_locale:options.requestSourceLocale==="unknown"};
}
export function createScenarioPreview({send,onChange}) {
  let generation=0,controller;
  const cancel=()=>{generation++;controller?.abort();};
  return {cancel,async run(body) {
    cancel();const token=generation;controller=new AbortController();
    const active=()=>token===generation;
    onChange({type:"start"});
    try {
      const data=await send(body,{signal:controller.signal,
        onText:text=>{if(active())onChange({type:"delta",text});},
        onProgress:event=>{if(active())onChange({type:"progress",event});}});
      if(active())onChange({type:"result",data});
    } catch(error) {if(active())onChange({type:"error",error});}
  }};
}
