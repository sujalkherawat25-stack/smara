import React, { useRef, useState } from 'react';
import { useFrame } from '@react-three/fiber';
import { Html } from '@react-three/drei';
import * as THREE from 'three';
import { SystemNode, StageId } from '../types';

interface HologramNodePodProps {
  node: SystemNode;
  isSelected: boolean;
  isProcessing: boolean;
  status?: 'pending' | 'processing' | 'completed' | 'bypassed' | 'updated';
  onSelect: (id: StageId) => void;
}

export const HologramNodePod: React.FC<HologramNodePodProps> = ({
  node,
  isSelected,
  isProcessing,
  status = 'pending',
  onSelect
}) => {
  const meshRef = useRef<THREE.Group>(null);
  const ringRef = useRef<THREE.Mesh>(null);
  const [hovered, setHovered] = useState(false);

  // Animate node rotation and hover float
  useFrame((state, delta) => {
    if (meshRef.current) {
      // Idle rotation
      meshRef.current.rotation.y += delta * (isSelected ? 0.8 : hovered ? 0.5 : 0.2);
      
      // Floating vertical bobbing animation
      const t = state.clock.getElapsedTime();
      meshRef.current.position.y = node.position[1] + Math.sin(t * 2 + node.stepNumber) * 0.25;
    }
    if (ringRef.current) {
      ringRef.current.rotation.z -= delta * 0.6;
    }
  });

  const getStatusColor = () => {
    if (status === 'processing' || isProcessing) return '#ffea00'; // Yellow pulse
    if (status === 'completed') return '#39ff14'; // Green
    if (status === 'bypassed') return '#ff3366'; // Pink/Red skip
    if (isSelected) return '#00f0ff'; // Cyan active
    return node.color;
  };

  const currentColor = getStatusColor();

  // Render 3D shape based on geometryType
  const renderGeometry = () => {
    switch (node.geometryType) {
      case 'hexagon_shield':
        return (
          <cylinderGeometry args={[1.6, 1.6, 1.2, 6]} />
        );
      case 'pulsing_core':
        return (
          <cylinderGeometry args={[1.4, 1.4, 2.0, 16]} />
        );
      case 'neural_sphere':
        return (
          <sphereGeometry args={[1.5, 32, 32]} />
        );
      case 'twin_gate':
        return (
          <boxGeometry args={[2.2, 2.4, 1.2]} />
        );
      case 'matrix_vault':
        return (
          <boxGeometry args={[1.8, 1.8, 1.8]} />
        );
      case 'graph_cluster':
        return (
          <icosahedronGeometry args={[1.6, 1]} />
        );
      case 'prism_engine':
        return (
          <octahedronGeometry args={[1.7, 0]} />
        );
      default:
        return <boxGeometry args={[1.5, 1.5, 1.5]} />;
    }
  };

  return (
    <group position={[node.position[0], node.position[1], node.position[2]]}>
      <group
        ref={meshRef}
        onClick={(e) => {
          e.stopPropagation();
          onSelect(node.id);
        }}
        onPointerOver={(e) => {
          e.stopPropagation();
          setHovered(true);
          document.body.style.cursor = 'pointer';
        }}
        onPointerOut={() => {
          setHovered(false);
          document.body.style.cursor = 'auto';
        }}
      >
        {/* Core Solid Holographic Mesh */}
        <mesh scale={hovered || isSelected ? 1.15 : 1.0}>
          {renderGeometry()}
          <meshStandardMaterial
            color={currentColor}
            emissive={currentColor}
            emissiveIntensity={isSelected ? 0.9 : hovered ? 0.7 : 0.4}
            transparent
            opacity={0.75}
            roughness={0.2}
            metalness={0.8}
            wireframe={false}
          />
        </mesh>

        {/* Outer Sci-Fi Wireframe Overlay */}
        <mesh scale={hovered || isSelected ? 1.25 : 1.1}>
          {renderGeometry()}
          <meshBasicMaterial
            color={currentColor}
            wireframe
            transparent
            opacity={isSelected ? 0.9 : 0.4}
          />
        </mesh>

        {/* Orbiting AR Ring */}
        <mesh ref={ringRef} rotation={[Math.PI / 3, 0, 0]}>
          <torusGeometry args={[2.2, 0.04, 16, 64]} />
          <meshBasicMaterial color={currentColor} transparent opacity={0.6} />
        </mesh>
      </group>

      {/* Floating 3D HUD Label */}
      <Html position={[0, 2.6, 0]} center distanceFactor={24}>
        <div
          onClick={() => onSelect(node.id)}
          className={`px-3 py-1.5 rounded-lg border backdrop-blur-md transition-all duration-300 cursor-pointer select-none text-center shadow-lg min-w-[140px] ${
            isSelected
              ? 'bg-cyan-950/80 border-cyan-400 text-cyan-200 shadow-cyan-500/50 scale-110 ring-2 ring-cyan-400'
              : hovered
              ? 'bg-slate-900/80 border-cyan-500/60 text-white scale-105'
              : 'bg-slate-950/70 border-slate-700/60 text-slate-300'
          }`}
        >
          <div className="flex items-center justify-center space-x-1.5 text-[10px] uppercase font-mono tracking-widest text-cyan-400 font-bold mb-0.5">
            <span>STEP 0{node.stepNumber}</span>
            {status === 'processing' && <span className="animate-ping w-1.5 h-1.5 rounded-full bg-yellow-400" />}
            {status === 'completed' && <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />}
            {status === 'bypassed' && <span className="w-1.5 h-1.5 rounded-full bg-pink-500" />}
          </div>
          <div className="text-xs font-bold font-sans tracking-wide text-white leading-tight">
            {node.title}
          </div>
          <div className="text-[10px] text-slate-400 font-mono mt-0.5 truncate max-w-[160px]">
            {node.subtitle}
          </div>
        </div>
      </Html>
    </group>
  );
};
