import NativeMarkdown from "./NativeMarkdown";
import type { Goal } from "./goalApi";

const percentage = (value: number | null | undefined) => value == null ? "Not measured" : `${Math.round(value * 100)}%`;

export default function NativeOutcome({ goal, onPreviewFile }: { goal: Goal; onPreviewFile?: (path: string) => void }) {
  const report = goal.checkpoint.outcome;
  if (!report) return null;
  return <details className="goal-outcome"><summary>Result checks · {report.passed ? "Evidence present — review required" : "Gaps remain"}</summary>
    <p>Citation excerpts: {percentage(report.metrics?.citation_support)} · Requirement evidence: {percentage(report.metrics?.evidence_coverage)} · {report.elapsed_seconds}s · {report.sources.length} pages read · {report.failed_pages.length} failed pages</p>
    <small>These checks verify observed evidence, not semantic correctness or full answer quality. “Not measured” is not a pass.</small>
    {report.checks.map(check => <p key={check.name}><strong>{check.passed ? "✓" : "○"} {check.name}</strong> — {check.detail}</p>)}
    {report.coverage?.map(requirement => <p key={requirement.id}>{requirement.evidence_present ? "✓" : "○"} {requirement.description} · Review whether its evidence satisfies the requirement.</p>)}
    {!!report.outputs?.length && <section aria-label="Verified output records"><strong>Recorded outputs</strong><small>Hashes describe the files at verification time; previews read the current project file.</small>
      {report.outputs.map(output => <div key={output.path}>{onPreviewFile ? <button onClick={() => onPreviewFile(output.path)}>Preview {output.path}</button> : <span>{output.path}</span>}<small>{output.bytes} bytes · SHA-256 {output.sha256}</small></div>)}
    </section>}
    {!!report.claims.length && <details><summary>Review claims and sources</summary>{report.claims.map((claim, index) => <blockquote key={index}>
      <p>{claim.claim}</p><NativeMarkdown text={`[Source](${claim.url})`} /><p>“{claim.quote}”</p><small>{claim.quote_found ? "Excerpt found in a fetched page" : "Excerpt not found"} · Semantic support needs review.</small>
    </blockquote>)}</details>}
    {report.failed_pages.map((page, index) => <p key={index}>Failed page: {page}</p>)}
    {report.tests?.filter(test => !test.passed).map(test => <details key={test.command}><summary>Failed test · {test.command}</summary><pre>{test.failure_excerpt || "No output excerpt recorded; inspect the native command transcript."}</pre></details>)}
  </details>;
}
