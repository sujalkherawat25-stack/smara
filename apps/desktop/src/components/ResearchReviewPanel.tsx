import { useState } from "react";
import { desktop, isNativeDesktop } from "../api";
import type { ResearchReview } from "../types";

function webUrl(value: string): string | undefined {
  try {
    const url = new URL(value);
    return ["https:", "http:"].includes(url.protocol) ? url.href : undefined;
  } catch { return undefined; }
}

export function ResearchReviewPanel({ review, topic, answer }: {
  review: ResearchReview; topic?: string; answer: string;
}) {
  const [watching, setWatching] = useState(false);
  const [notice, setNotice] = useState("");
  async function watch() {
    if (!topic || watching) return;
    setWatching(true);
    try {
      await desktop.addResearchWatch(topic, 24, { answer, research_review: review });
      setNotice("Daily refresh scheduled. Updates appear in Research watches.");
    } catch (error) { setNotice(String(error)); }
    finally { setWatching(false); }
  }
  return <details className="research-review-panel">
    <summary>Claim-to-source review · {review.supported_claims ?? 0}/{review.claim_count ?? 0} passage checks passed</summary>
    <p>{review.note || "Passage support is not proof that an answer is correct or complete. Inspect the sources."}</p>
    {(review.claims || []).map((claim, index) => <details key={index}>
      <summary>{claim.supported ? "Passage check passed" : "Review needed"} · {claim.claim}</summary>
      {claim.citations.map((citation) => <div key={citation.evidence_id}>
        {webUrl(citation.url)
          ? <a href={webUrl(citation.url)} target="_blank" rel="noopener noreferrer">{citation.url}</a>
          : <span>{citation.url}</span>}
        <p>Retrieved: {citation.retrieved_at || "Unknown"} · {citation.supported ? "Text support found" : "Insufficient support"}</p>
        <blockquote>{citation.passage}</blockquote>
      </div>)}
    </details>)}
    {(review.failures || []).length > 0 && <details>
      <summary>{review.failures.length} failed page(s)</summary>
      {review.failures.map((failure, index) => <p key={index}>{failure.url}: {failure.error}</p>)}
    </details>}
    {topic && isNativeDesktop && <button type="button" disabled={watching} onClick={() => void watch()}>
      {watching ? "Scheduling…" : "Refresh this research daily"}
    </button>}
    {notice && <p role="status">{notice}</p>}
  </details>;
}
