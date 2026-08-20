import { notFound } from "next/navigation";
import Link from "next/link";
import ContentViewer from "@/components/ContentViewer";

export const dynamic = "force-dynamic";

export default async function FulltextPage({ params }) {
  const { docId } = await params;

  const backendUrl = process.env.BACKEND_URL || "http://127.0.0.1:8000";
  const resp = await fetch(`${backendUrl}/api/documents/${docId}/fulltext`, {
    cache: "no-store",
  });

  if (!resp.ok) notFound();

  const text = await resp.text();

  return (
    <div className="okf-viewer">
      <Link className="back-link" href={`/documents/${docId}/okf`}>
        ← К списку концептов и чанков
      </Link>
      <h1>Весь документ</h1>
      <ContentViewer text={text} docId={docId} />
    </div>
  );
}