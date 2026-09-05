import Link from "next/link";
import { notFound } from "next/navigation";
import { DownloadIcon, FileTextIcon } from "@/components/icons";
import OkfFileList from "@/components/OkfFileList";
import { backendFetch } from "@/lib/backendFetch";
import { serverTranslator } from "@/i18n/server";

export const dynamic = "force-dynamic";

export default async function OkfListPage({ params }) {
  const { t } = await serverTranslator();
  const { docId } = await params;

  const [docResp, filesResp] = await Promise.all([
    backendFetch(`/api/documents/${docId}`),
    backendFetch(`/api/documents/${docId}/okf`),
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
        {t("okf.page.backToDocs")}
      </Link>
      <div className="okf-list-header">
        <div>
          <h1>{t("okf.page.h1")}</h1>
          <div className="okf-doc-name">{doc.filename}</div>
        </div>
        <div className="okf-doc-actions">
          <Link
            className="download-btn"
            href={`/documents/${docId}/fulltext`}
            title={t("okf.page.openFulltext")}
          >
            <FileTextIcon /> {t("okf.page.openFulltext")}
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
      <OkfFileList
        docId={docId}
        files={files}
        docStatus={doc.status}
        totalChunks={doc.total_chunks}
        processedChunks={doc.processed_chunks}
        currentChunk={doc.current_chunk}
      />
    </div>
  );
}