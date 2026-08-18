import Link from "next/link";
import { notFound } from "next/navigation";
import { DownloadIcon } from "@/components/icons";

export const dynamic = "force-dynamic";

export default async function OkfListPage({ params }) {
  const { docId } = await params;

  const backendUrl = process.env.BACKEND_URL || "http://localhost:8000";
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
          <h1>Список чанков</h1>
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
      {files.length === 0 ? (
        <p className="okf-empty">Чанки не найдены</p>
      ) : (
        <ul className="okf-list">
          {files.map((f) => (
            <li key={f.filename}>
              <Link
                href={`/documents/${docId}/okf/${encodeURIComponent(f.filename)}`}
                className="okf-list-item"
              >
                <span className="okf-title">{f.title || f.filename}</span>
                <span className="okf-meta">
                  <span className={`okf-type okf-type-${f.type}`}>{f.type}</span>
                  {f.tags && f.tags.length > 0 && (
                    <span className="okf-tags">Теги: {f.tags.join(", ")}</span>
                  )}
                  <span className="okf-size">{(f.size / 1024).toFixed(1)} КБ</span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}