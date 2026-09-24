import { notFound } from "next/navigation";
import Link from "next/link";
import ContentViewer from "@/components/ContentViewer";
import { backendFetch } from "@/lib/backendFetch";
import { serverTranslator } from "@/i18n/server";
import SourceLocationView from "@/components/SourceLocationView";

export const dynamic = "force-dynamic";

export default async function FulltextPage({ params, searchParams }) {
  const { t } = await serverTranslator();
  const { docId } = await params;
  const query = await searchParams;
  const concept = query?.concept;
  let sourceUnavailable = false;

  if (concept) {
    const [locationResp, chunksResp] = await Promise.all([
      backendFetch(`/api/documents/${docId}/concepts/${encodeURIComponent(concept)}/source-location`),
      backendFetch(`/api/documents/${docId}/fulltext/chunks`),
    ]);
    if (locationResp.ok && chunksResp.ok) {
      const [location, chunks] = await Promise.all([locationResp.json(), chunksResp.json()]);
      if (location.status !== "unavailable" && location.chunk_index != null && chunks.length) {
        return (
          <div className="okf-viewer source-fulltext-viewer">
            <Link className="back-link" href={`/documents/${docId}/okf`}>{t("okf.page.backToList")}</Link>
            <h1>{t("okf.page.fulltextH1")}</h1>
            <SourceLocationView docId={docId} chunks={chunks} location={location} heading={t("sourceLocation.title")} showAll />
          </div>
        );
      }
    }
    sourceUnavailable = true;
  }

  const resp = await backendFetch(`/api/documents/${docId}/fulltext`);

  if (!resp.ok) notFound();

  const text = await resp.text();

  return (
    <div className="okf-viewer">
      <Link className="back-link" href={`/documents/${docId}/okf`}>
        {t("okf.page.backToList")}
      </Link>
      <h1>{t("okf.page.fulltextH1")}</h1>
      {sourceUnavailable && <p className="source-location-note">{t("sourceLocation.unavailable")}</p>}
      <ContentViewer text={text} docId={docId} />
    </div>
  );
}
