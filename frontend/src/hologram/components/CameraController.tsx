import React, { useEffect, useRef } from 'react';
import { useFrame, useThree } from '@react-three/fiber';
import * as THREE from 'three';
import { StageId, CameraViewPreset } from '../types';
import { SYSTEM_NODES } from '../pipelineData';

interface CameraControllerProps {
  selectedNodeId: StageId | null;
  cameraPreset: CameraViewPreset | null;
  orbitControlsRef: any;
}

export const CameraController: React.FC<CameraControllerProps> = ({
  selectedNodeId,
  cameraPreset,
  orbitControlsRef
}) => {
  const { camera } = useThree();
  const targetPosRef = useRef<THREE.Vector3>(new THREE.Vector3(2, 16, 28));
  const targetLookAtRef = useRef<THREE.Vector3>(new THREE.Vector3(2, 1, -2));
  const isTransitioningRef = useRef(false);

  useEffect(() => {
    if (cameraPreset) {
      targetPosRef.current.set(...cameraPreset.position);
      targetLookAtRef.current.set(...cameraPreset.target);
      isTransitioningRef.current = true;
    } else if (selectedNodeId) {
      const node = SYSTEM_NODES.find((n) => n.id === selectedNodeId);
      if (node) {
        // Position camera directly facing the focused node pod
        targetPosRef.current.set(
          node.position[0] + 0,
          node.position[1] + 4,
          node.position[2] + 12
        );
        targetLookAtRef.current.set(
          node.position[0],
          node.position[1] + 1,
          node.position[2]
        );
        isTransitioningRef.current = true;
      }
    }
  }, [selectedNodeId, cameraPreset]);

  useFrame((_, delta) => {
    if (!isTransitioningRef.current) return;

    // Smooth lerp camera position
    camera.position.lerp(targetPosRef.current, delta * 3.0);

    // Smooth lerp OrbitControls target
    if (orbitControlsRef.current) {
      orbitControlsRef.current.target.lerp(targetLookAtRef.current, delta * 3.0);
      orbitControlsRef.current.update();
    }

    // Stop transition when close enough
    if (
      camera.position.distanceTo(targetPosRef.current) < 0.1 &&
      orbitControlsRef.current?.target.distanceTo(targetLookAtRef.current) < 0.1
    ) {
      isTransitioningRef.current = false;
    }
  });

  return null;
};
