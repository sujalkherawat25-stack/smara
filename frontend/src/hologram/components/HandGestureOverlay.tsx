import React from 'react';
import { GestureType } from '../hooks/useHandTracking';

interface HandGestureOverlayProps {
  isEnabled: boolean;
  isLoaded: boolean;
  handDetected: boolean;
  gesture: GestureType;
  pinchDistance: number;
  canvasRef: React.RefObject<HTMLCanvasElement>;
  errorMessage: string | null;
  onToggle: () => void;
}

export const HandGestureOverlay: React.FC<HandGestureOverlayProps> = ({
  isEnabled,
  isLoaded,
  handDetected,
  gesture,
  pinchDistance,
  canvasRef,
  errorMessage,
  onToggle
}) => {
  if (!isEnabled) return null;

  const getGestureLabel = () => {
    switch (gesture) {
      case 'rotate':
        return { text: '👋 PALM ROTATE', color: 'bg-cyan-500/20 text-cyan-300 border-cyan-400/50' };
      case 'pinch':
        return { text: `👌 PINCH ZOOM (${(pinchDistance * 10).toFixed(1)}x)`, color: 'bg-amber-500/20 text-amber-300 border-amber-400/50' };
      case 'fist':
        return { text: '✊ FIST PAUSE', color: 'bg-pink-500/20 text-pink-300 border-pink-400/50' };
      case 'pointing':
        return { text: '👆 INDEX POINT', color: 'bg-emerald-500/20 text-emerald-300 border-emerald-400/50' };
      default:
        return { text: '🔍 SEARCHING HAND', color: 'bg-slate-800 text-slate-400 border-slate-700' };
    }
  };

  const gestureBadge = getGestureLabel();

  return (
    <div className="absolute bottom-24 right-6 z-40 pointer-events-auto flex flex-col items-end space-y-2">
      {/* Stark HUD Hand Controller Panel */}
      <div className="relative w-48 h-36 bg-slate-950/90 backdrop-blur-xl border border-cyan-500/50 rounded-2xl p-2 shadow-2xl shadow-cyan-950/90 overflow-hidden flex flex-col justify-between">
        {/* Top Header */}
        <div className="flex items-center justify-between text-[10px] font-mono border-b border-slate-800/80 pb-1 px-1">
          <div className="flex items-center space-x-1.5">
            <span className={`w-2 h-2 rounded-full ${handDetected ? 'bg-emerald-400 animate-pulse' : 'bg-amber-400'}`} />
            <span className="text-cyan-300 font-bold uppercase tracking-wider">STARK HAND TRACK</span>
          </div>

          <button
            onClick={onToggle}
            className="text-slate-400 hover:text-white px-1 font-bold text-xs"
            title="Close Hand Controller"
          >
            ✕
          </button>
        </div>

        {/* Middle Canvas Overlay & Camera Stream */}
        <div className="relative flex-1 w-full my-1 rounded-lg overflow-hidden bg-slate-900/80 border border-slate-800 flex items-center justify-center">
          {!isLoaded && !errorMessage && (
            <div className="text-[10px] font-mono text-cyan-400 animate-pulse text-center p-2">
              INITIALIZING WEBCAM & AI VISION MODEL…
            </div>
          )}

          {errorMessage && (
            <div className="text-[10px] font-mono text-pink-400 text-center p-2">
              ⚠️ {errorMessage}
            </div>
          )}

          <canvas
            ref={canvasRef}
            width={176}
            height={100}
            className="w-full h-full object-cover transform -scale-x-100"
          />

          {!handDetected && isLoaded && (
            <div className="absolute inset-0 flex items-center justify-center text-[10px] font-mono text-cyan-300/80 bg-slate-950/60 backdrop-blur-[1px] text-center px-2">
              Raise hand to camera…
            </div>
          )}
        </div>

        {/* Bottom Gesture Badge */}
        <div className="px-1">
          <div className={`text-[10px] font-mono px-2 py-0.5 rounded-lg border text-center font-bold tracking-wider ${gestureBadge.color}`}>
            {gestureBadge.text}
          </div>
        </div>
      </div>
    </div>
  );
};
