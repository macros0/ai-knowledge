import { notFound } from "next/navigation";
import Link from "next/link";
import ContentViewer from "@/components/ContentViewer";
import { backendFetch } from "@/lib/backendFetch";

export const dynamic = "force-dynamic";

export default async function ChunkPage({ params }) {
  const { docId, chunkIndex } = await params;
  const index = Number(chunkIndex);

  const resp = await backendFetch(`/api/documents/${docId}/chunks/${index}`);

  if (!resp.ok) notFound();

  const text = await resp.text();

  return (
    <div className="okf-viewer">
      <Link className="back-link" href={`/documents/${docId}/okf`}>
        ← К списку концептов и чанков
      </Link>
      <h1>Чанк {index + 1}</h1>
      <ContentViewer text={text} docId={docId} />
    </div>
  );
}