import React, { useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';
import { SYSTEM_NODES } from '../pipelineData';

export const HologramFloorGrid: React.FC = () => {
  const ringGroupRef = useRef<THREE.Group>(null);
  const particleGroupRef = useRef<THREE.Points>(null);

  // Animate rotation of AR target rings and background floating particles
  useFrame((_, delta) => {
    if (ringGroupRef.current) {
      ringGroupRef.current.rotation.y += delta * 0.15;
    }
    if (particleGroupRef.current) {
      particleGroupRef.current.rotation.y += delta * 0.03;
    }
  });

  // Generate random ambient 3D hologram particles
  const particleCount = 200;
  const positions = new Float32Array(particleCount * 3);
  const colors = new Float32Array(particleCount * 3);

  for (let i = 0; i < particleCount; i++) {
    positions[i * 3] = (Math.random() - 0.5) * 80;
    positions[i * 3 + 1] = Math.random() * 20 - 2;
    positions[i * 3 + 2] = (Math.random() - 0.5) * 50;

    // Cyan / Gold / Magenta particles
    const r = Math.random() > 0.5 ? 0.0 : 1.0;
    const g = Math.random() > 0.3 ? 0.9 : 0.2;
    const b = 1.0;
    colors[i * 3] = r;
    colors[i * 3 + 1] = g;
    colors[i * 3 + 2] = b;
  }

  return (
    <group>
      {/* Primary Cyber Grid Floor */}
      <gridHelper
        args={[100, 50, 0x00f0ff, 0x0a2540]}
        position={[0, -2, 0]}
      />

      {/* Secondary Fine Grid */}
      <gridHelper
        args={[100, 100, 0x00aaff, 0x051525]}
        position={[0, -2.01, 0]}
      />

      {/* Rotating Concentric AR Target Circles around nodes */}
      <group ref={ringGroupRef}>
        {SYSTEM_NODES.map((node) => (
          <group key={`ring-${node.id}`} position={[node.position[0], -1.95, node.position[2]]}>
            {/* Outer AR Ring */}
            <mesh rotation={[-Math.PI / 2, 0, 0]}>
              <ringGeometry args={[2.5, 2.6, 32]} />
              <meshBasicMaterial color={node.color} transparent opacity={0.6} side={THREE.DoubleSide} />
            </mesh>
            {/* Inner Dashed Ring */}
            <mesh rotation={[-Math.PI / 2, 0, 0]}>
              <ringGeometry args={[1.8, 1.85, 16]} />
              <meshBasicMaterial color="#ffffff" transparent opacity={0.4} side={THREE.DoubleSide} />
            </mesh>
          </group>
        ))}
      </group>

      {/* Floating 3D Sci-Fi Hologram Particles */}
      <points ref={particleGroupRef}>
        <bufferGeometry>
          <bufferAttribute
            attach="attributes-position"
            count={particleCount}
            array={positions}
            itemSize={3}
          />
          <bufferAttribute
            attach="attributes-color"
            count={particleCount}
            array={colors}
            itemSize={3}
          />
        </bufferGeometry>
        <pointsMaterial
          size={0.25}
          vertexColors
          transparent
          opacity={0.7}
          blending={THREE.AdditiveBlending}
        />
      </points>

      {/* Linking Energy Beams (Line paths connecting pipeline nodes) */}
      {SYSTEM_NODES.map((node, index) => {
        if (index === SYSTEM_NODES.length - 1) return null;
        const nextNode = SYSTEM_NODES[index + 1];
        const points = [
          new THREE.Vector3(...node.position),
          new THREE.Vector3(
            (node.position[0] + nextNode.position[0]) / 2,
            Math.max(node.position[1], nextNode.position[1]) + 2,
            (node.position[2] + nextNode.position[2]) / 2
          ),
          new THREE.Vector3(...nextNode.position)
        ];
        const curve = new THREE.CatmullRomCurve3(points);
        const geometry = new THREE.BufferGeometry().setFromPoints(curve.getPoints(50));
        const material = new THREE.LineBasicMaterial({ color: new THREE.Color(node.color as any), transparent: true, opacity: 0.5 });
        const lineObj = new THREE.Line(geometry, material);

        return (
          <primitive key={`link-${node.id}-${nextNode.id}`} object={lineObj} />
        );
      })}
    </group>
  );
};
