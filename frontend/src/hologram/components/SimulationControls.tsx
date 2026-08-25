import React, { useState } from 'react';
import { StageId, SimulationPreset } from '../types';
import { SYSTEM_NODES, SIMULATION_PRESETS } from '../pipelineData';

interface SimulationControlsProps {
  isPlaying: boolean;
  activeStageId: StageId | null;
  speed: number;
  currentPreset: SimulationPreset;
  customMessage: string;
  onTogglePlay: () => void;
  onStepForward: () => void;
  onStepBackward: () => void;
  onChangeSpeed: (speed: number) => void;
  onSelectPreset: (preset: SimulationPreset) => void;
  onRunCustomMessage: (msg: string) => void;
}

export const SimulationControls: React.FC<SimulationControlsProps> = ({
  isPlaying,
  activeStageId,
  speed,
  currentPreset,
  customMessage,
  onTogglePlay,
  onStepForward,
  onStepBackward,
  onChangeSpeed,
  onSelectPreset,
  onRunCustomMessage
}) => {
  const [inputText, setInputText] = useState(customMessage);
  const [showInputDrawer, setShowInputDrawer] = useState(false);

  const activeIndex = SYSTEM_NODES.findIndex((n) => n.id === activeStageId);
  const progressPercent = activeIndex !== -1 ? ((activeIndex + 1) / SYSTEM_NODES.length) * 100 : 0;

  const handleSubmitCustom = (e: React.FormEvent) => {
    e.preventDefault();
    if (!inputText.trim()) return;
    onRunCustomMessage(inputText.trim());
    setShowInputDrawer(false);
  };

  return (
    <div className="absolute bottom-6 left-1/2 -translate-x-1/2 z-30 w-full max-w-4xl px-4 pointer-events-none">
      <div className="pointer-events-auto bg-slate-950/90 backdrop-blur-2xl border border-cyan-500/30 rounded-2xl p-3.5 shadow-2xl shadow-cyan-950/80">
        {/* Top Progress Line */}
        <div className="w-full bg-slate-900 h-1.5 rounded-full overflow-hidden mb-3 border border-slate-800">
          <div
            className="bg-gradient-to-r from-cyan-500 via-teal-400 to-amber-400 h-full transition-all duration-300 shadow-lg shadow-cyan-400"
            style={{ width: `${progressPercent}%` }}
          />
        </div>

        <div className="flex flex-col sm:flex-row items-center justify-between gap-3">
          {/* Preset Scenario Selector */}
          <div className="flex items-center space-x-2">
            <span className="text-[10px] font-mono text-cyan-400 uppercase font-bold tracking-wider">
              SCENARIO:
            </span>
            <select
              value={currentPreset.id}
              onChange={(e) => {
                const found = SIMULATION_PRESETS.find((p) => p.id === e.target.value);
                if (found) onSelectPreset(found);
              }}
              className="bg-slate-900 border border-slate-700 text-slate-200 text-xs font-mono rounded-lg px-2.5 py-1.5 focus:outline-none focus:border-cyan-400"
            >
              {SIMULATION_PRESETS.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.title}
                </option>
              ))}
            </select>

            <button
              onClick={() => setShowInputDrawer(!showInputDrawer)}
              className="px-2.5 py-1.5 bg-cyan-950 hover:bg-cyan-900 border border-cyan-500/40 text-cyan-300 text-xs font-mono rounded-lg transition-all font-semibold"
            >
              + Custom Ingest
            </button>
          </div>

          {/* Player Controls (Step Prev, Play/Pause, Step Next) */}
          <div className="flex items-center space-x-2">
            <button
              onClick={onStepBackward}
              disabled={activeIndex <= 0}
              className="px-3 py-1.5 bg-slate-900 hover:bg-slate-800 border border-slate-700 text-slate-300 text-xs font-mono rounded-lg disabled:opacity-30 disabled:cursor-not-allowed transition-all font-bold"
            >
              Step Back
            </button>

            <button
              onClick={onTogglePlay}
              className={`px-5 py-1.5 rounded-lg text-xs font-mono font-bold tracking-wider uppercase transition-all shadow-lg ${
                isPlaying
                  ? 'bg-amber-500 hover:bg-amber-400 text-slate-950 shadow-amber-500/50 ring-2 ring-amber-400'
                  : 'bg-cyan-500 hover:bg-cyan-400 text-slate-950 shadow-cyan-500/50 ring-2 ring-cyan-400'
              }`}
            >
              {isPlaying ? 'PAUSE' : 'RUN DIAGNOSTIC'}
            </button>

            <button
              onClick={onStepForward}
              disabled={activeIndex >= SYSTEM_NODES.length - 1}
              className="px-3 py-1.5 bg-slate-900 hover:bg-slate-800 border border-slate-700 text-slate-300 text-xs font-mono rounded-lg disabled:opacity-30 disabled:cursor-not-allowed transition-all font-bold"
            >
              Step Next
            </button>

            {/* Speed Toggle */}
            <div className="flex items-center space-x-1 bg-slate-900 border border-slate-800 rounded-lg p-0.5 text-[10px] font-mono ml-2">
              {[1, 2, 5].map((s) => (
                <button
                  key={s}
                  onClick={() => onChangeSpeed(s)}
                  className={`px-2 py-0.5 rounded ${
                    speed === s
                      ? 'bg-cyan-500 text-slate-950 font-bold'
                      : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  {s}x
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Custom Input Drawer Modal */}
        {showInputDrawer && (
          <form onSubmit={handleSubmitCustom} className="mt-3 pt-3 border-t border-slate-800 flex items-center space-x-2">
            <input
              type="text"
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              placeholder="Enter custom user message (e.g. My favorite food is sushi and I live in Paris)..."
              className="flex-1 bg-slate-900 border border-cyan-500/50 text-white text-xs font-mono px-3 py-2 rounded-lg focus:outline-none focus:ring-1 focus:ring-cyan-400"
            />
            <button
              type="submit"
              className="px-4 py-2 bg-gradient-to-r from-cyan-500 to-teal-400 text-slate-950 text-xs font-mono font-bold rounded-lg hover:brightness-110 transition-all"
            >
              Simulate 3D Ingest
            </button>
          </form>
        )}
      </div>
    </div>
  );
};
