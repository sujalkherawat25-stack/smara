import React from 'react';
import { CameraViewPreset } from '../types';
import { CAMERA_PRESETS } from '../pipelineData';

interface HologramHUDHeaderProps {
  onSelectCameraPreset: (preset: CameraViewPreset) => void;
  onResetView: () => void;
  isHandTrackingEnabled?: boolean;
  onToggleHandTracking?: () => void;
  onCloseHologram?: () => void;
}

export const HologramHUDHeader: React.FC<HologramHUDHeaderProps> = ({
  onSelectCameraPreset,
  onResetView,
  isHandTrackingEnabled,
  onToggleHandTracking,
  onCloseHologram
}) => {
  return (
    <header className="absolute top-0 left-0 right-0 z-30 p-4 pointer-events-none flex items-center justify-between">
      {/* Top Left Title HUD */}
      <div className="pointer-events-auto flex items-center space-x-3 bg-slate-950/80 backdrop-blur-xl border border-cyan-500/30 px-4 py-2.5 rounded-xl shadow-2xl shadow-cyan-950/50">
        <div className="relative flex items-center justify-center w-8 h-8 rounded-lg bg-cyan-950 border border-cyan-400/50">
          <div className="w-3 h-3 rounded-full bg-cyan-400 animate-pulse shadow-lg shadow-cyan-400" />
          <div className="absolute inset-0 rounded-lg border border-cyan-400/30 animate-ping opacity-30" />
        </div>
        <div>
          <div className="flex items-center space-x-2">
            <h1 className="text-sm font-black font-sans tracking-wider text-transparent bg-clip-text bg-gradient-to-r from-cyan-300 via-teal-200 to-blue-400 uppercase">
              Syntarus Memory Lab
            </h1>
            <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-cyan-500/20 text-cyan-300 border border-cyan-400/30 font-bold">
              SYSTEM DESIGN GUIDE
            </span>
          </div>
          <p className="text-[10px] text-slate-400 font-mono tracking-tight">
            INTERACTIVE VIEW OF THE LIVE MEMORY PIPELINE
          </p>
        </div>
      </div>

      {/* Middle Store Status HUD */}
      <div className="hidden lg:flex pointer-events-auto items-center space-x-4 bg-slate-950/70 backdrop-blur-md border border-slate-800 px-4 py-2 rounded-xl text-xs font-mono">
        <div className="flex items-center space-x-1.5">
          <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
          <span className="text-slate-400">REDIS:</span>
          <span className="text-emerald-300 font-bold">CONNECTED</span>
        </div>
        <div className="h-3 w-[1px] bg-slate-800" />
        <div className="flex items-center space-x-1.5">
          <span className="w-2 h-2 rounded-full bg-cyan-400 animate-pulse" />
          <span className="text-slate-400">QDRANT:</span>
          <span className="text-cyan-300 font-bold">HNSW INDEX</span>
        </div>
        <div className="h-3 w-[1px] bg-slate-800" />
        <div className="flex items-center space-x-1.5">
          <span className="w-2 h-2 rounded-full bg-purple-400 animate-pulse" />
          <span className="text-slate-400">NEO4J:</span>
          <span className="text-purple-300 font-bold">TEMPORAL GRAPH</span>
        </div>
        <div className="h-3 w-[1px] bg-slate-800" />
        <div className="flex items-center space-x-1.5">
          <span className="w-2 h-2 rounded-full bg-amber-400 animate-pulse" />
          <span className="text-slate-400">EXTRACTION:</span>
          <span className="text-amber-300 font-bold">PROVIDER CHAIN READY</span>
        </div>
      </div>

      {/* Top Right Camera Presets & Actions */}
      <div className="pointer-events-auto flex items-center space-x-2">
        {onToggleHandTracking && (
          <button
            onClick={onToggleHandTracking}
            className={`px-3 py-1.5 border text-xs font-mono rounded-xl transition-all font-bold flex items-center space-x-1.5 shadow-lg ${
              isHandTrackingEnabled
                ? 'bg-gradient-to-r from-cyan-500 to-teal-400 text-slate-950 border-cyan-300 shadow-cyan-500/50 scale-105 ring-2 ring-cyan-400'
                : 'bg-slate-900/90 text-cyan-300 border-cyan-500/40 hover:bg-cyan-950'
            }`}
          >
            <span>🖐️</span>
            <span>{isHandTrackingEnabled ? 'HAND CONTROL: ON' : 'HAND CONTROL'}</span>
          </button>
        )}

        <div className="hidden sm:flex items-center bg-slate-950/80 backdrop-blur-md border border-slate-800 rounded-xl p-1 text-[11px] font-mono">
          {CAMERA_PRESETS.map((preset) => (
            <button
              key={preset.name}
              onClick={() => onSelectCameraPreset(preset)}
              className="px-2.5 py-1 rounded-lg text-slate-300 hover:text-cyan-300 hover:bg-cyan-950/60 transition-all font-medium whitespace-nowrap"
            >
              {preset.name}
            </button>
          ))}
        </div>

        <button
          onClick={onResetView}
          className="px-3 py-1.5 bg-slate-900/90 hover:bg-slate-800 border border-slate-700 text-slate-200 text-xs font-mono rounded-xl transition-all font-semibold shadow-lg hover:border-cyan-500/50"
        >
          Reset View
        </button>

        {onCloseHologram && (
          <button
            onClick={onCloseHologram}
            className="px-3 py-1.5 bg-red-950/80 hover:bg-red-900 border border-red-700 text-red-200 text-xs font-mono rounded-xl transition-all font-semibold shadow-lg"
          >
            Exit 3D Lab
          </button>
        )}
      </div>
    </header>
  );
};
