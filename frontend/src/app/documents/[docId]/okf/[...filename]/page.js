import { notFound, redirect } from "next/navigation";
import Link from "next/link";
import ContentViewer from "@/components/ContentViewer";
import { DownloadIcon } from "@/components/icons";
import { backendFetch } from "@/lib/backendFetch";
import { serverTranslator } from "@/i18n/server";

export const dynamic = "force-dynamic";

function decodeSegment(segment) {
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}

export async function generateMetadata({ params }) {
  const { filename } = await params;
  const decodedPath = filename.map(decodeSegment).join("/");
  return { title: `${decodedPath} — OKF Knowledge Service` };
}

export default async function OkfFilePage({ params }) {
  const { t } = await serverTranslator();
  const { docId, filename } = await params;
  const decodedPath = filename.map(decodeSegment);
  const filePath = decodedPath.map(encodeURIComponent).join("/");

  const [docResp, resp] = await Promise.all([
    backendFetch(`/api/documents/${docId}`),
    backendFetch(`/api/documents/${docId}/okf/${filePath}`),
  ]);

  if (!docResp.ok) notFound();
  if (!resp.ok) redirect(`/documents/${docId}/okf`);
  const doc = await docResp.json();

  const text = await resp.text();

  const chunkMatch = text.match(/^chunk_index:\s*(\d+)/m);
  const chunkIndex = chunkMatch ? Number(chunkMatch[1]) : null;

  return (
    <div className="okf-viewer">
      <Link className="back-link" href={`/documents/${docId}/okf`}>
        {t("okf.page.backToList")}
      </Link>
      <div className="okf-doc-bar">
        <span className="okf-doc-name">{doc.filename}</span>
        <div className="okf-doc-actions">
          <Link className="okf-doc-open" href={`/documents/${docId}/fulltext`}>
            {t("okf.page.openFulltext")} →
          </Link>
          <a
            className="download-btn"
            href={`/api/documents/${docId}/download`}
            download
            title={t("okf.page.download")}
          >
            <DownloadIcon /> {t("okf.page.download")}
          </a>
        </div>
      </div>
      <h1>{decodedPath.join("/")}</h1>
      {chunkIndex != null && (
        <Link
          className="okf-chunk-link"
          href={`/documents/${docId}/chunks/${chunkIndex}`}
        >
          {t("okf.page.linkedChunk", { index: chunkIndex + 1 })}
        </Link>
      )}
      <ContentViewer text={text} docId={docId} stripFrontmatter />
    </div>
  );
}
