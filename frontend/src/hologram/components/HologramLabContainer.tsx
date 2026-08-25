import React, { useState, useEffect, useRef } from 'react';
import { StageId, SimulationPreset, CameraViewPreset, SimulationStepData } from '../types';
import { SYSTEM_NODES, SIMULATION_PRESETS } from '../pipelineData';
import { HologramCanvas } from './HologramCanvas';
import { HologramHUDHeader } from './HologramHUDHeader';
import { SimulationControls } from './SimulationControls';
import { HologramInspectorPanel } from './HologramInspectorPanel';
import { HandGestureOverlay } from './HandGestureOverlay';
import { useHandTracking } from '../hooks/useHandTracking';

interface HologramLabContainerProps {
  onClose?: () => void;
}

export const HologramLabContainer: React.FC<HologramLabContainerProps> = ({ onClose }) => {
  const [selectedNodeId, setSelectedNodeId] = useState<StageId | null>('fact_extraction');
  const [activeStageId, setActiveStageId] = useState<StageId | null>('pii_redaction');
  const [isPlaying, setIsPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [currentPreset, setCurrentPreset] = useState<SimulationPreset>(SIMULATION_PRESETS[0]);
  const [customMessage, setCustomMessage] = useState(SIMULATION_PRESETS[0].rawUserMessage);
  const [cameraPreset, setCameraPreset] = useState<CameraViewPreset | null>(null);

  // Hand Tracking Hook
  const handTracking = useHandTracking();
  const {
    isEnabled: isHandTrackingEnabled,
    isLoaded: isHandTrackingLoaded,
    handDetected,
    gesture,
    pinchDistance,
    canvasRef: handCanvasRef,
    errorMessage: handErrorMessage,
    toggleHandTracking
  } = handTracking;

  const [simulationSteps, setSimulationSteps] = useState<Record<StageId, SimulationStepData>>(
    SIMULATION_PRESETS[0].steps
  );

  const timerRef = useRef<any>(null);

  // Auto Step Simulation Player
  useEffect(() => {
    if (!isPlaying) {
      if (timerRef.current) clearInterval(timerRef.current);
      return;
    }

    const intervalTime = Math.max(800 / speed, 300);

    timerRef.current = setInterval(() => {
      setActiveStageId((prevStageId) => {
        const currentIndex = SYSTEM_NODES.findIndex((n) => n.id === prevStageId);
        if (currentIndex === -1 || currentIndex >= SYSTEM_NODES.length - 1) {
          setIsPlaying(false);
          return SYSTEM_NODES[SYSTEM_NODES.length - 1].id;
        }

        const nextStage = SYSTEM_NODES[currentIndex + 1].id;
        setSelectedNodeId(nextStage);
        return nextStage;
      });
    }, intervalTime);

    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [isPlaying, speed]);

  const handleSelectNode = (id: StageId) => {
    setSelectedNodeId(id);
    setActiveStageId(id);
    setCameraPreset(null);
  };

  const handleTogglePlay = () => {
    if (isPlaying) {
      setIsPlaying(false);
    } else {
      // If at end, restart from step 1
      if (activeStageId === SYSTEM_NODES[SYSTEM_NODES.length - 1].id) {
        setActiveStageId(SYSTEM_NODES[0].id);
        setSelectedNodeId(SYSTEM_NODES[0].id);
      }
      setIsPlaying(true);
    }
  };

  const handleStepForward = () => {
    setIsPlaying(false);
    const currentIndex = SYSTEM_NODES.findIndex((n) => n.id === activeStageId);
    if (currentIndex < SYSTEM_NODES.length - 1) {
      const nextId = SYSTEM_NODES[currentIndex + 1].id;
      setActiveStageId(nextId);
      setSelectedNodeId(nextId);
    }
  };

  const handleStepBackward = () => {
    setIsPlaying(false);
    const currentIndex = SYSTEM_NODES.findIndex((n) => n.id === activeStageId);
    if (currentIndex > 0) {
      const prevId = SYSTEM_NODES[currentIndex - 1].id;
      setActiveStageId(prevId);
      setSelectedNodeId(prevId);
    }
  };

  const handleSelectPreset = (preset: SimulationPreset) => {
    setIsPlaying(false);
    setCurrentPreset(preset);
    setCustomMessage(preset.rawUserMessage);
    setSimulationSteps(preset.steps);
    setActiveStageId(SYSTEM_NODES[0].id);
    setSelectedNodeId(SYSTEM_NODES[0].id);
  };

  const handleRunCustomMessage = (msg: string) => {
    setIsPlaying(false);
    setCustomMessage(msg);
    // Create custom steps object
    const customSteps: Record<StageId, SimulationStepData> = { ...currentPreset.steps };
    setSimulationSteps(customSteps);
    setActiveStageId(SYSTEM_NODES[0].id);
    setSelectedNodeId(SYSTEM_NODES[0].id);
    setIsPlaying(true);
  };

  const handleResetView = () => {
    setSelectedNodeId(null);
    setCameraPreset({
      name: 'Full Arc',
      position: [2, 16, 28],
      target: [2, 1, -2]
    });
  };

  const selectedNode = SYSTEM_NODES.find((n) => n.id === selectedNodeId) || null;

  return (
    <div className="w-screen h-screen relative overflow-hidden bg-[#040814]">
      {/* HUD Header */}
      <HologramHUDHeader
        onSelectCameraPreset={(preset) => {
          setCameraPreset(preset);
          setSelectedNodeId(null);
        }}
        onResetView={handleResetView}
        isHandTrackingEnabled={isHandTrackingEnabled}
        onToggleHandTracking={toggleHandTracking}
        onCloseHologram={onClose}
      />

      {/* Hand Gesture Stark Overlay */}
      <HandGestureOverlay
        isEnabled={isHandTrackingEnabled}
        isLoaded={isHandTrackingLoaded}
        handDetected={handDetected}
        gesture={gesture}
        pinchDistance={pinchDistance}
        canvasRef={handCanvasRef}
        errorMessage={handErrorMessage}
        onToggle={toggleHandTracking}
      />

      {/* 3D WebGL Canvas */}
      <HologramCanvas
        selectedNodeId={selectedNodeId}
        activeStageId={activeStageId}
        isPlaying={isPlaying}
        simulationSteps={simulationSteps}
        cameraPreset={cameraPreset}
        handState={handTracking}
        onSelectNode={handleSelectNode}
      />

      {/* Bottom Playback Toolbar */}
      <SimulationControls
        isPlaying={isPlaying}
        activeStageId={activeStageId}
        speed={speed}
        currentPreset={currentPreset}
        customMessage={customMessage}
        onTogglePlay={handleTogglePlay}
        onStepForward={handleStepForward}
        onStepBackward={handleStepBackward}
        onChangeSpeed={setSpeed}
        onSelectPreset={handleSelectPreset}
        onRunCustomMessage={handleRunCustomMessage}
      />

      {/* Deep Inspection Side Drawer */}
      <HologramInspectorPanel
        selectedNode={selectedNode}
        stepData={selectedNodeId ? simulationSteps[selectedNodeId] : undefined}
        onClose={() => setSelectedNodeId(null)}
        onSelectNode={handleSelectNode}
      />
    </div>
  );
};
