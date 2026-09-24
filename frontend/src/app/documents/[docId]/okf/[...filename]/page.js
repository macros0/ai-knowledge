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
  const conceptSlug = decodedPath.at(-1)?.replace(/\.md$/i, "");

  const [docResp, resp, sourceResp] = await Promise.all([
    backendFetch(`/api/documents/${docId}`),
    backendFetch(`/api/documents/${docId}/okf/${filePath}`),
    backendFetch(`/api/documents/${docId}/concepts/${encodeURIComponent(conceptSlug)}/source-location`),
  ]);

  if (!docResp.ok) notFound();
  if (!resp.ok) redirect(`/documents/${docId}/okf`);
  const doc = await docResp.json();

  const text = await resp.text();
  const sourceLocation = sourceResp.ok ? await sourceResp.json() : null;

  const chunkMatch = text.match(/^chunk_index:\s*(\d+)/m);
  const chunkIndex = chunkMatch ? Number(chunkMatch[1]) : null;
  const conceptQuery = `?concept=${encodeURIComponent(conceptSlug)}`;

  return (
    <div className="okf-viewer">
      <Link className="back-link" href={`/documents/${docId}/okf`}>
        {t("okf.page.backToList")}
      </Link>
      <div className="okf-doc-bar">
        <span className="okf-doc-name">{doc.filename}</span>
        <div className="okf-doc-actions">
          <Link className="okf-doc-open" href={`/documents/${docId}/fulltext${conceptQuery}`}>
            {t("sourceLocation.openInDocument")} →
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
      <aside className="source-evidence-summary">
        <strong>{t("sourceLocation.title")}</strong>
        {(sourceLocation?.status === "exact" || sourceLocation?.status === "recovered") && sourceLocation.spans?.length ? (
          <>
            <span>{t(sourceLocation.status === "recovered" ? "sourceLocation.recovered" : "sourceLocation.exact", { index: (sourceLocation.chunk_index ?? chunkIndex ?? 0) + 1 })}</span>
            <blockquote>{sourceLocation.spans[0].quote}</blockquote>
          </>
        ) : sourceLocation?.status === "chunk" && sourceLocation.chunk_index != null ? (
          <span>{t("sourceLocation.chunkOnly", { index: sourceLocation.chunk_index + 1 })}</span>
        ) : (
          <span>{t("sourceLocation.unavailable")}</span>
        )}
      </aside>
      {chunkIndex != null && (
        <Link
          className="okf-chunk-link"
          href={`/documents/${docId}/chunks/${chunkIndex}${conceptQuery}`}
        >
          {t("sourceLocation.openInChunk", { index: chunkIndex + 1 })}
        </Link>
      )}
      <ContentViewer text={text} docId={docId} stripFrontmatter />
    </div>
  );
}
