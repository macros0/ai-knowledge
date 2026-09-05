import { notFound } from "next/navigation";
import Link from "next/link";
import ContentViewer from "@/components/ContentViewer";
import { backendFetch } from "@/lib/backendFetch";
import { serverTranslator } from "@/i18n/server";

export const dynamic = "force-dynamic";

export default async function ChunkPage({ params }) {
  const { t } = await serverTranslator();
  const { docId, chunkIndex } = await params;
  const index = Number(chunkIndex);

  const resp = await backendFetch(`/api/documents/${docId}/chunks/${index}`);

  if (!resp.ok) notFound();

  const text = await resp.text();

  return (
    <div className="okf-viewer">
      <Link className="back-link" href={`/documents/${docId}/okf`}>
        {t("okf.page.backToList")}
      </Link>
      <h1>{t("okf.page.chunkH1", { index: index + 1 })}</h1>
      <ContentViewer text={text} docId={docId} />
    </div>
  );
}