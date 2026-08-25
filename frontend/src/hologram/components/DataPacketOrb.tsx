import React, { useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';
import { StageId } from '../types';
import { SYSTEM_NODES } from '../pipelineData';

interface DataPacketOrbProps {
  activeStageId: StageId | null;
  isPlaying: boolean;
}

export const DataPacketOrb: React.FC<DataPacketOrbProps> = ({ activeStageId, isPlaying }) => {
  const orbRef = useRef<THREE.Group>(null);
  const particleTrailRef = useRef<THREE.Points>(null);

  const activeIndex = SYSTEM_NODES.findIndex((n) => n.id === activeStageId);
  const targetNode = activeIndex !== -1 ? SYSTEM_NODES[activeIndex] : SYSTEM_NODES[0];

  useFrame((_, delta) => {
    if (!orbRef.current) return;

    // Smoothly interpolate orb position towards active target node position
    const targetPos = new THREE.Vector3(...targetNode.position);
    orbRef.current.position.lerp(targetPos, delta * (isPlaying ? 4.0 : 2.0));

    // Rotate internal orb mesh
    orbRef.current.rotation.x += delta * 3.0;
    orbRef.current.rotation.y += delta * 2.0;

    if (particleTrailRef.current) {
      particleTrailRef.current.rotation.z += delta * 1.5;
    }
  });

  return (
    <group ref={orbRef} position={[SYSTEM_NODES[0].position[0], SYSTEM_NODES[0].position[1], SYSTEM_NODES[0].position[2]]}>
      {/* Primary Glowing Packet Orb Core */}
      <mesh>
        <sphereGeometry args={[0.65, 32, 32]} />
        <meshStandardMaterial
          color="#ffea00"
          emissive="#ffea00"
          emissiveIntensity={1.5}
          roughness={0.1}
        />
      </mesh>

      {/* Outer Pulse Shield */}
      <mesh scale={1.4}>
        <sphereGeometry args={[0.65, 16, 16]} />
        <meshBasicMaterial color="#ffffff" wireframe transparent opacity={0.6} />
      </mesh>

      {/* Energy Light Source */}
      <pointLight color="#ffea00" intensity={3} distance={8} />
    </group>
  );
};
