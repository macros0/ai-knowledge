// Keep native details state: CSS shows the action that matches the open attribute.
export default function ChatDisclosureSummary({ showLabel, hideLabel, meta }) {
  return <summary className="chat-disclosure-summary">
    <span className="chat-disclosure-show source-toggle-action">{showLabel}</span>
    <span className="chat-disclosure-hide source-toggle-action">{hideLabel}</span>
    {meta && <span className="source-toggle-counts">{meta}</span>}
  </summary>;
}
