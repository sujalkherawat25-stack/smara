import React, { useRef } from 'react';
import { Canvas } from '@react-three/fiber';
import { OrbitControls } from '@react-three/drei';
import { StageId, CameraViewPreset, SimulationStepData } from '../types';
import { SYSTEM_NODES } from '../pipelineData';
import { HologramFloorGrid } from './HologramFloorGrid';
import { HologramNodePod } from './HologramNodePod';
import { DataPacketOrb } from './DataPacketOrb';
import { CameraController } from './CameraController';
import { HandCameraController } from './HandCameraController';
import { HandTrackingState } from '../hooks/useHandTracking';

interface HologramCanvasProps {
  selectedNodeId: StageId | null;
  activeStageId: StageId | null;
  isPlaying: boolean;
  simulationSteps: Record<StageId, SimulationStepData>;
  cameraPreset: CameraViewPreset | null;
  handState?: HandTrackingState;
  onSelectNode: (id: StageId) => void;
}

export const HologramCanvas: React.FC<HologramCanvasProps> = ({
  selectedNodeId,
  activeStageId,
  isPlaying,
  simulationSteps,
  cameraPreset,
  handState,
  onSelectNode
}) => {
  const orbitControlsRef = useRef<any>(null);

  return (
    <div className="w-full h-full relative bg-[#040814] overflow-hidden select-none">
      <Canvas
        camera={{ position: [2, 16, 28], fov: 45 }}
        gl={{ antialias: true, alpha: false, powerPreference: 'high-performance' }}
      >
        <color attach="background" args={['#040814']} />
        
        {/* Holographic Stark Ambient & Point Lighting */}
        <ambientLight intensity={0.4} />
        <directionalLight position={[10, 20, 15]} intensity={1.2} color="#00f0ff" />
        <pointLight position={[-15, 10, -5]} intensity={1.5} color="#ff0055" />
        <pointLight position={[15, 10, -5]} intensity={1.5} color="#39ff14" />

        {/* Orbit Camera Controls */}
        <OrbitControls
          ref={orbitControlsRef}
          enablePan={true}
          enableZoom={true}
          enableRotate={true}
          maxPolarAngle={Math.PI / 2 - 0.05} // Prevent going below grid floor
          minDistance={5}
          maxDistance={60}
        />

        {/* Camera Lerp Controller */}
        <CameraController
          selectedNodeId={selectedNodeId}
          cameraPreset={cameraPreset}
          orbitControlsRef={orbitControlsRef}
        />

        {/* AI Hand Gesture Camera Controller */}
        {handState && (
          <HandCameraController
            handState={handState}
            onSelectNode={onSelectNode}
            orbitControlsRef={orbitControlsRef}
          />
        )}

        {/* Floor Grid & Cyber Environment */}
        <HologramFloorGrid />

        {/* 3D Holographic Pipeline Node Pods */}
        {SYSTEM_NODES.map((node) => {
          const stepData = simulationSteps[node.id];
          return (
            <HologramNodePod
              key={node.id}
              node={node}
              isSelected={selectedNodeId === node.id}
              isProcessing={activeStageId === node.id}
              status={stepData?.status}
              onSelect={onSelectNode}
            />
          );
        })}

        {/* Animated Data Packet Energy Orb */}
        <DataPacketOrb activeStageId={activeStageId} isPlaying={isPlaying} />
      </Canvas>
    </div>
  );
};
