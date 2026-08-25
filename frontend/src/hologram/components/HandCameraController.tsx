import React, { useRef } from 'react';
import { useFrame, useThree } from '@react-three/fiber';
import * as THREE from 'three';
import { HandTrackingState } from '../hooks/useHandTracking';
import { SYSTEM_NODES } from '../pipelineData';
import { StageId } from '../types';

interface HandCameraControllerProps {
  handState: HandTrackingState;
  onSelectNode: (id: StageId) => void;
  orbitControlsRef: React.RefObject<any>;
}

export const HandCameraController: React.FC<HandCameraControllerProps> = ({
  handState,
  onSelectNode,
  orbitControlsRef
}) => {
  const { camera } = useThree();
  const prevPinchRef = useRef<number | null>(null);
  const raycasterRef = useRef<THREE.Raycaster>(new THREE.Raycaster());
  const hoverTimerRef = useRef<{ nodeId: StageId; time: number } | null>(null);
  const pointerMeshRef = useRef<THREE.Mesh>(null);

  useFrame(() => {
    if (!handState.isEnabled || !handState.handDetected) {
      prevPinchRef.current = null;
      if (pointerMeshRef.current) pointerMeshRef.current.visible = false;
      return;
    }

    const { deltaPos, pinchDistance, gesture, landmarks } = handState;

    // 1. PALM ROTATION (Hand dragging left/right/up/down) - Dampened sensitivity
    if (gesture === 'rotate' && (Math.abs(deltaPos.x) > 0.003 || Math.abs(deltaPos.y) > 0.003)) {
      const target = orbitControlsRef.current?.target || new THREE.Vector3(2, 1, -2);
      const offset = camera.position.clone().sub(target);

      const spherical = new THREE.Spherical().setFromVector3(offset);
      // Smoothed & lowered multipliers (3.5 / 2.2) for calm, non-twitchy rotation
      spherical.theta += deltaPos.x * 3.5;
      spherical.phi = THREE.MathUtils.clamp(
        spherical.phi - deltaPos.y * 2.2,
        0.1,
        Math.PI / 2 - 0.05
      );

      offset.setFromSpherical(spherical);
      camera.position.copy(target).add(offset);
      camera.lookAt(target);

      if (orbitControlsRef.current) {
        orbitControlsRef.current.update();
      }
    }

    // 2. PINCH ZOOM (Pinching index + thumb together/apart) - Dampened sensitivity
    if (gesture === 'pinch') {
      if (prevPinchRef.current !== null) {
        const pinchDelta = pinchDistance - prevPinchRef.current;
        if (Math.abs(pinchDelta) > 0.006) {
          const target = orbitControlsRef.current?.target || new THREE.Vector3(2, 1, -2);
          const dir = camera.position.clone().sub(target).normalize();
          // Lower zoom multiplier (10.0) for controlled zooming
          const zoomAmount = pinchDelta * 10.0;
          
          const newPos = camera.position.clone().addScaledVector(dir, zoomAmount);
          const dist = newPos.distanceTo(target);
          if (dist >= 6 && dist <= 55) {
            camera.position.copy(newPos);
          }
        }
      }
      prevPinchRef.current = pinchDistance;
    } else {
      prevPinchRef.current = null;
    }

    // 3. INDEX FINGER RAYCAST POINTER & SELECTION
    if (landmarks.length > 8) {
      const indexTip = landmarks[8]; // Landmark 8 = Index Fingertip
      const ndcPoint = new THREE.Vector2(
        (1 - indexTip.x) * 2 - 1,
        -(indexTip.y * 2 - 1)
      );

      // Position 3D holographic laser cursor in screen space
      if (pointerMeshRef.current) {
        pointerMeshRef.current.visible = true;
        const vector = new THREE.Vector3(ndcPoint.x, ndcPoint.y, 0.5);
        vector.unproject(camera);
        const dir = vector.sub(camera.position).normalize();
        const distance = 12; // Distance in front of camera
        pointerMeshRef.current.position.copy(camera.position).add(dir.multiplyScalar(distance));
      }

      // Raycast into scene to detect hovered node pod
      raycasterRef.current.setFromCamera(ndcPoint, camera);
      
      // Test against each node pod position (precise 2.0 unit radius)
      let hoveredNode: StageId | null = null;
      for (const node of SYSTEM_NODES) {
        const nodePos = new THREE.Vector3(...node.position);
        const ray = raycasterRef.current.ray;
        const distToRay = ray.distanceToPoint(nodePos);
        if (distToRay < 2.0) {
          hoveredNode = node.id;
          break;
        }
      }

      if (hoveredNode) {
        const now = Date.now();
        if (!hoverTimerRef.current || hoverTimerRef.current.nodeId !== hoveredNode) {
          hoverTimerRef.current = { nodeId: hoveredNode, time: now };
        } else if (now - hoverTimerRef.current.time > 750) {
          // Dwell for 750ms -> Trigger Selection!
          onSelectNode(hoveredNode);
          hoverTimerRef.current = null;
        }
      } else {
        hoverTimerRef.current = null;
      }
    } else {
      if (pointerMeshRef.current) pointerMeshRef.current.visible = false;
    }
  });

  return (
    /* Holographic 3D Laser Pointer Reticle */
    <mesh ref={pointerMeshRef} visible={false}>
      <sphereGeometry args={[0.35, 16, 16]} />
      <meshBasicMaterial color="#ffea00" transparent opacity={0.85} />
      <pointLight color="#ffea00" intensity={2} distance={5} />
    </mesh>
  );
};
