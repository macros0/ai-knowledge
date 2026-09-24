import { notFound, redirect } from "next/navigation";
import Link from "next/link";
import ContentViewer from "@/components/ContentViewer";
import { backendFetch } from "@/lib/backendFetch";
import { serverTranslator } from "@/i18n/server";
import SourceLocationView from "@/components/SourceLocationView";

export const dynamic = "force-dynamic";

export default async function ChunkPage({ params, searchParams }) {
  const { t } = await serverTranslator();
  const { docId, chunkIndex } = await params;
  const query = await searchParams;
  const index = Number(chunkIndex);
  let sourceUnavailable = false;

  if (query?.concept) {
    const locationResp = await backendFetch(`/api/documents/${docId}/concepts/${encodeURIComponent(query.concept)}/source-location`);
    if (locationResp.ok) {
      const location = await locationResp.json();
      if (location.chunk_index != null && location.chunk_index !== index) {
        redirect(`/documents/${docId}/chunks/${location.chunk_index}?concept=${encodeURIComponent(query.concept)}`);
      }
      if (location.chunk_index === index && location.status !== "unavailable") {
        const resp = await backendFetch(`/api/documents/${docId}/chunks/${index}`);
        if (!resp.ok) notFound();
        const text = await resp.text();
        return (
          <div className="okf-viewer source-chunk-viewer">
            <Link className="back-link" href={`/documents/${docId}/okf`}>{t("okf.page.backToList")}</Link>
            <h1>{t("okf.page.chunkH1", { index: index + 1 })}</h1>
            <SourceLocationView docId={docId} chunks={[{ chunk_index: index, content: text }]} location={location} />
          </div>
        );
      }
    }
    sourceUnavailable = true;
  }

  const resp = await backendFetch(`/api/documents/${docId}/chunks/${index}`);

  if (!resp.ok) notFound();

  const text = await resp.text();

  return (
    <div className="okf-viewer">
      <Link className="back-link" href={`/documents/${docId}/okf`}>
        {t("okf.page.backToList")}
      </Link>
      <h1>{t("okf.page.chunkH1", { index: index + 1 })}</h1>
      {sourceUnavailable && <p className="source-location-note">{t("sourceLocation.unavailable")}</p>}
      <ContentViewer text={text} docId={docId} />
    </div>
  );
}
