import { notFound, redirect } from "next/navigation";
import Link from "next/link";
import SourceContentViewer from "@/components/SourceContentViewer";
import { backendFetch } from "@/lib/backendFetch";
import { serverTranslator } from "@/i18n/server";
import SourceLocationView from "@/components/SourceLocationView";
import DocumentMarkupButton from "@/components/DocumentMarkupButton";

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
        const [resp, sourcesResp] = await Promise.all([
          backendFetch(`/api/documents/${docId}/chunks/${index}`),
          backendFetch(`/api/documents/${docId}/sources`),
        ]);
        if (!resp.ok) notFound();
        const text = await resp.text();
        const sourceData = sourcesResp.ok ? await sourcesResp.json() : { sources: [] };
        return (
          <div className="okf-viewer source-chunk-viewer">
            <Link className="back-link" href={`/documents/${docId}/okf?source=${encodeURIComponent(location.source_id || "root")}`}>{t("okf.page.backToList")}</Link>
            <div className="document-viewer-heading">
              <h1>
                {t("okf.page.chunkH1", { index: index + 1 })}
              </h1>
              <DocumentMarkupButton docId={docId} />
            </div>
            <SourceLocationView docId={docId} chunks={[{ chunk_index: index, source_id: location.source_id, content: text }]} location={location} sources={sourceData.sources || []} />
          </div>
        );
      }
    }
    sourceUnavailable = true;
  }

  const [chunksResp, sourcesResp] = await Promise.all([
    backendFetch(`/api/documents/${docId}/fulltext/chunks`),
    backendFetch(`/api/documents/${docId}/sources`),
  ]);

  if (!chunksResp.ok || !sourcesResp.ok) notFound();

  const [chunks, sourceData] = await Promise.all([chunksResp.json(), sourcesResp.json()]);
  const chunk = chunks.find(({ chunk_index }) => chunk_index === index);

  if (!chunk) notFound();

  return (
    <div className="okf-viewer">
      <Link className="back-link" href={`/documents/${docId}/okf`}>
        {t("okf.page.backToList")}
      </Link>
      <div className="document-viewer-heading">
        <h1>{t("okf.page.chunkH1", { index: index + 1 })}</h1>
        <DocumentMarkupButton docId={docId} />
      </div>
      {sourceUnavailable && <p className="source-location-note">{t("sourceLocation.unavailable")}</p>}
      <SourceContentViewer docId={docId} chunks={[chunk]} sources={sourceData.sources || []} />
    </div>
  );
}
