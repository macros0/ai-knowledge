import Link from "next/link";
import { notFound } from "next/navigation";
import { DownloadIcon } from "@/components/icons";
import OkfFileList from "@/components/OkfFileList";

export const dynamic = "force-dynamic";

export default async function OkfListPage({ params }) {
  const { docId } = await params;

  const backendUrl = process.env.BACKEND_URL || "http://127.0.0.1:8000";
  const [docResp, filesResp] = await Promise.all([
    fetch(`${backendUrl}/api/documents/${docId}`, { cache: "no-store" }),
    fetch(`${backendUrl}/api/documents/${docId}/okf`, { cache: "no-store" }),
  ]);

  if (!docResp.ok) notFound();
  const doc = await docResp.json();

  let files = [];
  if (filesResp.ok) {
    files = await filesResp.json();
  }

  return (
    <div className="okf-list-page">
      <Link className="back-link" href="/">
        ← Назад к документам
      </Link>
      <div className="okf-list-header">
        <div>
          <h1>Концепты и чанки</h1>
          <div className="okf-doc-name">{doc.filename}</div>
        </div>
        <a
          className="download-btn"
          href={`/api/documents/${docId}/download`}
          download
          title="Скачать исходный файл"
        >
          <DownloadIcon /> Скачать
        </a>
      </div>
      <OkfFileList docId={docId} files={files} />
    </div>
  );
}