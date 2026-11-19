import { useState } from "react";
import { askView } from "./askCard";
import { supportsApproval } from "./nativeView";
import { requestQuestions } from "./chatStatus";

type Request = { id: string | number; method: string; params: Record<string, unknown> };
type Question = NonNullable<ReturnType<typeof requestQuestions>>[number];

/**
 * Takes the composer's place while Smara waits for the owner: one clear question, what exactly will happen,
 * and the answers as buttons. Nothing is approved by default; Esc does not approve.
 */
export default function NativeAskCard({ request, waiting, relevant, agentName, onDecide, onAnswer, onOpenOwner }: {
  request: Request; waiting: number; relevant: boolean; agentName: string;
  onDecide: (allow: boolean) => void; onAnswer: (answers: Record<string, string>) => void; onOpenOwner?: () => void;
}) {
  const [showDetail, setShowDetail] = useState(false);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const questions: Question[] | null = request.method === "item/tool/requestUserInput" ? requestQuestions(request.params) ?? null : null;
  const more = waiting > 1 ? <span className="ask-more">{waiting - 1} more waiting</span> : null;

  if (!relevant) return <section className="ask-card" aria-label="Waiting for another conversation">
    <header><span className="ask-badge">Waiting</span>{more}</header>
    <h3>Another conversation needs your answer</h3>
    <p>This request belongs to a different chat, so it can't be answered here.</p>
    {onOpenOwner && <div className="ask-actions"><button className="primary" onClick={onOpenOwner}>Open that chat</button></div>}
  </section>;

  if (questions) return <section className="ask-card" aria-label={`${agentName} has a question`}>
    <header><span className="ask-badge">{agentName} asks</span>{more}</header>
    {questions.map(question => <fieldset key={question.id} className="ask-question">
      <legend>{question.question}</legend>
      {!!question.options?.length && <div className="ask-options">{question.options.map(option => <button type="button" key={option.label} title={option.description}
        aria-pressed={answers[question.id] === option.label} onClick={() => setAnswers(current => ({ ...current, [question.id]: option.label }))}>
        <strong>{option.label}</strong>{option.description && <small>{option.description}</small>}</button>)}</div>}
      <input aria-label={question.question} type={question.isSecret ? "password" : "text"} placeholder={question.options?.length ? "Or type your own answer" : "Type your answer"}
        value={answers[question.id] ?? ""} onChange={event => setAnswers(current => ({ ...current, [question.id]: event.target.value }))} />
    </fieldset>)}
    <div className="ask-actions"><button className="primary" disabled={questions.some(question => !(answers[question.id] ?? "").trim())} onClick={() => onAnswer(answers)}>Continue</button></div>
  </section>;

  const view = askView(request);
  const canAnswer = supportsApproval(request.method, request.params);
  return <section className={`ask-card${view.risky ? " ask-risky" : ""}`} aria-label={view.title} data-kind={view.kind}>
    <header><span className="ask-badge">{view.risky ? "Needs your OK · outward step" : "Needs your OK"}</span>{more}</header>
    <h3>{view.title}</h3>
    <p className="ask-summary">{view.summary}</p>
    {view.detail && <><button type="button" className="text-button ask-toggle" aria-expanded={showDetail} onClick={() => setShowDetail(value => !value)}>{showDetail ? "Hide details" : "Show details"}</button>
      {showDetail && <pre className="ask-detail">{view.detail}</pre>}</>}
    <div className="ask-actions">
      {canAnswer ? <><button className="primary" autoFocus onClick={() => onDecide(true)}>Allow</button><button onClick={() => onDecide(false)}>Don't allow</button></>
        : <><p>This kind of request can't be approved from Smara Desktop.</p><button onClick={() => onDecide(false)}>Decline</button></>}
    </div>
  </section>;
}
