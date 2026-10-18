import { useI18n } from "@/i18n/LocaleContext";

export default function ReferenceBatchResult({ results }) {
  const { t } = useI18n();
  if (!results.length) return null;
  const count = results.filter(item => ["reviewed", "deleted", "unchanged"].includes(item.status)).length;
  return <div className="reference-edit-notice">
    <p role="status">{t("reference.batchFinished", { count, total: results.length })}</p>
    <details><summary>{t("reference.batchDetails")}</summary><ul>
      {results.map((item, index) => <li key={`${item.tag_id}:${index}`}>
        {item.name || item.tag_id}: {t(`reference.batchStatus.${item.status}`)}
      </li>)}
    </ul></details>
  </div>;
}
