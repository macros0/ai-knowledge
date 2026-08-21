export default function Loading() {
  return (
    <div className="okf-list-page">
      <span className="back-link">← Назад к документам</span>
      <div className="okf-list-header">
        <div>
          <h1>Концепты и чанки</h1>
        </div>
      </div>
      <ul className="okf-list">
        {Array.from({ length: 10 }, (_, i) => (
          <li key={i} className="okf-item-row">
            <div className="okf-list-item okf-skeleton">
              <span className="okf-skeleton-bar w-60" />
              <span className="okf-skeleton-bar w-30" />
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
