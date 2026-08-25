import React, { useState } from 'react';
import { SystemNode, SimulationStepData, StageId } from '../types';
import { SYSTEM_NODES } from '../pipelineData';

interface HologramInspectorPanelProps {
  selectedNode: SystemNode | null;
  stepData?: SimulationStepData;
  onClose: () => void;
  onSelectNode: (id: StageId) => void;
}

export const HologramInspectorPanel: React.FC<HologramInspectorPanelProps> = ({
  selectedNode,
  stepData,
  onClose,
  onSelectNode
}) => {
  const [activeTab, setActiveTab] = useState<'overview' | 'code' | 'data' | 'math'>('overview');

  if (!selectedNode) return null;

  const currentIndex = SYSTEM_NODES.findIndex((n) => n.id === selectedNode.id);
  const prevNode = currentIndex > 0 ? SYSTEM_NODES[currentIndex - 1] : null;
  const nextNode = currentIndex < SYSTEM_NODES.length - 1 ? SYSTEM_NODES[currentIndex + 1] : null;

  return (
    <div className="absolute top-20 right-6 z-30 w-full max-w-lg pointer-events-auto bg-slate-950/95 backdrop-blur-2xl border border-cyan-500/40 rounded-2xl shadow-2xl shadow-cyan-950/90 overflow-hidden text-slate-200 flex flex-col max-h-[calc(100vh-140px)] animate-in fade-in slide-in-from-right-10 duration-300">
      {/* Panel Header */}
      <div className="p-4 border-b border-slate-800 bg-slate-900/60 flex items-center justify-between">
        <div className="flex items-center space-x-3">
          <div
            className="w-10 h-10 rounded-xl flex items-center justify-center font-bold text-sm font-mono border"
            style={{
              backgroundColor: `${selectedNode.color}20`,
              borderColor: selectedNode.color,
              color: selectedNode.color
            }}
          >
            0{selectedNode.stepNumber}
          </div>
          <div>
            <div className="flex items-center space-x-2">
              <h2 className="text-sm font-bold font-sans text-white">{selectedNode.title}</h2>
              {stepData?.status && (
                <span className={`text-[10px] font-mono px-2 py-0.5 rounded-full font-bold uppercase ${
                  stepData.status === 'completed' ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30' :
                  stepData.status === 'processing' ? 'bg-yellow-500/20 text-yellow-400 border border-yellow-500/30 animate-pulse' :
                  stepData.status === 'bypassed' ? 'bg-pink-500/20 text-pink-400 border border-pink-500/30' :
                  'bg-slate-800 text-slate-400'
                }`}>
                  {stepData.status}
                </span>
              )}
            </div>
            <p className="text-[11px] text-cyan-400 font-mono mt-0.5">{selectedNode.subtitle}</p>
          </div>
        </div>

        <button
          onClick={onClose}
          className="w-8 h-8 rounded-lg bg-slate-900 border border-slate-700 text-slate-400 hover:text-white hover:border-slate-500 flex items-center justify-center transition-all"
        >
          ✕
        </button>
      </div>

      {/* Navigation Tabs */}
      <div className="flex items-center border-b border-slate-800 bg-slate-900/40 px-4 text-xs font-mono">
        {(['overview', 'code', 'data', 'math'] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-3 py-2.5 capitalize font-semibold border-b-2 transition-all ${
              activeTab === tab
                ? 'border-cyan-400 text-cyan-300 bg-cyan-950/40'
                : 'border-transparent text-slate-400 hover:text-slate-200'
            }`}
          >
            {tab === 'overview' && '📌 Purpose'}
            {tab === 'code' && '💻 Code Logic'}
            {tab === 'data' && '🔄 Data Flow'}
            {tab === 'math' && '📐 Math & Formulas'}
          </button>
        ))}
      </div>

      {/* Panel Scrollable Content */}
      <div className="p-4 overflow-y-auto space-y-4 text-xs font-sans leading-relaxed flex-1">
        {/* Tab 1: Overview */}
        {activeTab === 'overview' && (
          <div className="space-y-4">
            <div className="p-3 rounded-xl bg-slate-900/80 border border-slate-800 text-slate-300">
              <span className="text-[10px] font-mono uppercase text-cyan-400 font-bold block mb-1">
                SYSTEM TAGLINE
              </span>
              <p className="italic text-cyan-100">{selectedNode.tagline}</p>
            </div>

            <div>
              <h3 className="text-xs font-bold font-mono text-slate-200 uppercase tracking-wider mb-2">
                Architectural Purpose
              </h3>
              <p className="text-slate-300">{selectedNode.purpose}</p>
            </div>

            <div>
              <h3 className="text-xs font-bold font-mono text-slate-200 uppercase tracking-wider mb-2">
                Deep Internal Operations
              </h3>
              <ul className="space-y-2">
                {selectedNode.deepExplanation.map((point, idx) => (
                  <li key={idx} className="flex items-start space-x-2 text-slate-300 bg-slate-900/50 p-2 rounded-lg border border-slate-800/60">
                    <span className="text-cyan-400 font-mono font-bold mt-0.5">•</span>
                    <span>{point}</span>
                  </li>
                ))}
              </ul>
            </div>

            {/* Key Metrics Cards */}
            <div>
              <h3 className="text-xs font-bold font-mono text-slate-200 uppercase tracking-wider mb-2">
                Operational signals
              </h3>
              <div className="grid grid-cols-3 gap-2 font-mono">
                {selectedNode.keyMetrics.map((m, idx) => (
                  <div key={idx} className="p-2 bg-slate-900 rounded-lg border border-slate-800 text-center">
                    <div className="text-[10px] text-slate-400 uppercase">{m.label}</div>
                    <div className="text-xs font-bold text-cyan-300 mt-0.5">{m.value}</div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* Tab 2: Code Logic */}
        {activeTab === 'code' && (
          <div className="space-y-4 font-mono">
            {selectedNode.codeSnippets.map((snippet, idx) => (
              <div key={idx} className="space-y-1.5">
                <div className="flex items-center justify-between text-[11px] text-slate-400 bg-slate-900 p-2 rounded-t-lg border-t border-x border-slate-800">
                  <span className="font-bold text-cyan-300">{snippet.filename}</span>
                  <span className="text-[10px] text-slate-500">{snippet.filepath}</span>
                </div>
                <pre className="p-3 bg-slate-950 border border-slate-800 rounded-b-lg text-emerald-300 text-[11px] overflow-x-auto leading-normal">
                  <code>{snippet.code}</code>
                </pre>
                <p className="text-[10px] text-slate-400 italic px-1">{snippet.description}</p>
              </div>
            ))}
          </div>
        )}

        {/* Tab 3: Data Transformation */}
        {activeTab === 'data' && (
          <div className="space-y-4 font-mono">
            <div className="space-y-1.5">
              <div className="text-[11px] font-bold text-amber-400 uppercase">
                Input Payload: {selectedNode.dataTransformation.inputLabel}
              </div>
              <pre className="p-3 bg-slate-950 border border-slate-800 rounded-xl text-amber-200 text-[11px] overflow-x-auto">
                <code>{JSON.stringify(selectedNode.dataTransformation.inputJson, null, 2)}</code>
              </pre>
            </div>

            <div className="flex justify-center my-1 text-cyan-400 font-bold text-base animate-bounce">
              ↓
            </div>

            <div className="space-y-1.5">
              <div className="text-[11px] font-bold text-cyan-400 uppercase">
                Output Payload: {selectedNode.dataTransformation.outputLabel}
              </div>
              <pre className="p-3 bg-slate-950 border border-cyan-900/50 rounded-xl text-cyan-200 text-[11px] overflow-x-auto">
                <code>{JSON.stringify(selectedNode.dataTransformation.outputJson, null, 2)}</code>
              </pre>
            </div>
          </div>
        )}

        {/* Tab 4: Math & Formulas */}
        {activeTab === 'math' && (
          <div className="space-y-4">
            {selectedNode.mathFormulas.map((formula, idx) => (
              <div key={idx} className="p-3.5 bg-slate-900 border border-cyan-500/30 rounded-xl space-y-2">
                <div className="text-xs font-bold font-mono text-cyan-300 uppercase">{formula.title}</div>
                <div className="p-2.5 bg-slate-950 rounded-lg text-amber-300 font-mono text-center overflow-x-auto text-xs font-bold border border-slate-800">
                  {formula.latex}
                </div>
                <p className="text-slate-300 text-xs">{formula.explanation}</p>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Footer Navigation */}
      <div className="p-3 border-t border-slate-800 bg-slate-900/80 flex items-center justify-between text-xs font-mono">
        <button
          onClick={() => prevNode && onSelectNode(prevNode.id)}
          disabled={!prevNode}
          className="px-3 py-1.5 bg-slate-900 hover:bg-slate-800 border border-slate-700 text-slate-300 rounded-lg disabled:opacity-30 disabled:cursor-not-allowed transition-all"
        >
          ← 0{prevNode?.stepNumber} {prevNode?.title}
        </button>

        <button
          onClick={() => nextNode && onSelectNode(nextNode.id)}
          disabled={!nextNode}
          className="px-3 py-1.5 bg-slate-900 hover:bg-slate-800 border border-slate-700 text-slate-300 rounded-lg disabled:opacity-30 disabled:cursor-not-allowed transition-all"
        >
          0{nextNode?.stepNumber} {nextNode?.title} →
        </button>
      </div>
    </div>
  );
};
