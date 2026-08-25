import { useEffect, useRef, useState, useCallback } from 'react';

export interface HandLandmark {
  x: number;
  y: number;
  z: number;
}

export type GestureType = 'none' | 'rotate' | 'pinch' | 'fist' | 'pointing';

export interface HandTrackingState {
  isEnabled: boolean;
  isLoaded: boolean;
  handDetected: boolean;
  palmPos: { x: number; y: number };
  deltaPos: { x: number; y: number };
  pinchDistance: number;
  gesture: GestureType;
  landmarks: HandLandmark[];
  errorMessage: string | null;
}

export const useHandTracking = () => {
  const [isEnabled, setIsEnabled] = useState(false);
  const [isLoaded, setIsLoaded] = useState(false);
  const [handDetected, setHandDetected] = useState(false);
  const [palmPos, setPalmPos] = useState({ x: 0.5, y: 0.5 });
  const [deltaPos, setDeltaPos] = useState({ x: 0, y: 0 });
  const [pinchDistance, setPinchDistance] = useState(1.0);
  const [gesture, setGesture] = useState<GestureType>('none');
  const [landmarks, setLandmarks] = useState<HandLandmark[]>([]);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const prevPalmRef = useRef<{ x: number; y: number } | null>(null);
  const prevDeltaRef = useRef<{ x: number; y: number } | null>(null);
  const cameraRef = useRef<any>(null);
  const handsRef = useRef<any>(null);

  const toggleHandTracking = useCallback(() => {
    setIsEnabled((prev) => !prev);
  }, []);

  useEffect(() => {
    if (!isEnabled) {
      if (cameraRef.current) {
        cameraRef.current.stop();
        cameraRef.current = null;
      }
      if (handsRef.current) {
        handsRef.current.close();
        handsRef.current = null;
      }
      setIsLoaded(false);
      setHandDetected(false);
      setGesture('none');
      setLandmarks([]);
      return;
    }

    let isSubscribed = true;

    async function initMediaPipe() {
      try {
        const { Hands } = await import('@mediapipe/hands');
        const { Camera } = await import('@mediapipe/camera_utils');

        if (!isSubscribed) return;

        // Create hidden video element for webcam capture
        const videoElement = document.createElement('video');
        videoElement.setAttribute('playsinline', '');
        videoElement.style.display = 'none';
        document.body.appendChild(videoElement);
        videoRef.current = videoElement;

        const hands = new Hands({
          locateFile: (file) => `https://cdn.jsdelivr.net/npm/@mediapipe/hands/${file}`
        });

        hands.setOptions({
          maxNumHands: 1,
          modelComplexity: 1,
          minDetectionConfidence: 0.6,
          minTrackingConfidence: 0.6
        });

        hands.onResults((results: any) => {
          if (!isSubscribed) return;

          if (results.multiHandLandmarks && results.multiHandLandmarks.length > 0) {
            const hand = results.multiHandLandmarks[0] as HandLandmark[];
            setLandmarks(hand);
            setHandDetected(true);

            // Landmark 0: Wrist, Landmark 5: Index MCP, Landmark 17: Pinky MCP
            const wrist = hand[0];
            const indexMcp = hand[5];
            const pinkyMcp = hand[17];

            const currentPalmX = (wrist.x + indexMcp.x + pinkyMcp.x) / 3;
            const currentPalmY = (wrist.y + indexMcp.y + pinkyMcp.y) / 3;

            // Compute Delta with Low-Pass Smoothing Filter
            if (prevPalmRef.current) {
              const rawDx = currentPalmX - prevPalmRef.current.x;
              const rawDy = currentPalmY - prevPalmRef.current.y;
              // Smooth out tiny micro-jitters using exponential moving average
              const smoothDx = (prevDeltaRef.current?.x || 0) * 0.5 + rawDx * 0.5;
              const smoothDy = (prevDeltaRef.current?.y || 0) * 0.5 + rawDy * 0.5;
              prevDeltaRef.current = { x: smoothDx, y: smoothDy };
              setDeltaPos({ x: smoothDx, y: smoothDy });
            }
            prevPalmRef.current = { x: currentPalmX, y: currentPalmY };
            setPalmPos({ x: currentPalmX, y: currentPalmY });

            // Landmark 4: Thumb Tip, Landmark 8: Index Tip
            const thumbTip = hand[4];
            const indexTip = hand[8];
            const middleTip = hand[12];
            const ringTip = hand[16];
            const pinkyTip = hand[20];

            // Calculate 3D Pinch Distance (Thumb Tip to Index Tip)
            const pinchDist = Math.hypot(
              thumbTip.x - indexTip.x,
              thumbTip.y - indexTip.y,
              thumbTip.z - indexTip.z
            );
            setPinchDistance(pinchDist);

            // Calculate distance of fingertips to wrist to identify Fist
            const distIndexWrist = Math.hypot(indexTip.x - wrist.x, indexTip.y - wrist.y);
            const distMiddleWrist = Math.hypot(middleTip.x - wrist.x, middleTip.y - wrist.y);
            const distRingWrist = Math.hypot(ringTip.x - wrist.x, ringTip.y - wrist.y);
            const distPinkyWrist = Math.hypot(pinkyTip.x - wrist.x, pinkyTip.y - wrist.y);

            const isFist =
              distIndexWrist < 0.22 &&
              distMiddleWrist < 0.22 &&
              distRingWrist < 0.22 &&
              distPinkyWrist < 0.22;

            const isPointing =
              distIndexWrist > 0.35 &&
              distMiddleWrist < 0.25 &&
              distRingWrist < 0.25 &&
              distPinkyWrist < 0.25;

            const isPinching = pinchDist < 0.08;

            if (isFist) {
              setGesture('fist');
            } else if (isPinching) {
              setGesture('pinch');
            } else if (isPointing) {
              setGesture('pointing');
            } else {
              setGesture('rotate');
            }
          } else {
            setHandDetected(false);
            setGesture('none');
            setLandmarks([]);
            prevPalmRef.current = null;
          }

          // Render canvas holographic overlay if canvas exists
          if (canvasRef.current && results.multiHandLandmarks) {
            drawHolographicHand(canvasRef.current, results.multiHandLandmarks[0]);
          }
        });

        handsRef.current = hands;

        const camera = new Camera(videoElement, {
          onFrame: async () => {
            if (handsRef.current && videoElement) {
              await handsRef.current.send({ image: videoElement });
            }
          },
          width: 320,
          height: 240
        });

        await camera.start();
        cameraRef.current = camera;
        setIsLoaded(true);
      } catch (err: any) {
        console.error('Failed to initialize Hand Tracking:', err);
        setErrorMessage(err?.message || 'Webcam access denied or unavailable.');
        setIsEnabled(false);
      }
    }

    initMediaPipe();

    return () => {
      isSubscribed = false;
      if (cameraRef.current) cameraRef.current.stop();
      if (handsRef.current) handsRef.current.close();
      if (videoRef.current && videoRef.current.parentNode) {
        videoRef.current.parentNode.removeChild(videoRef.current);
      }
    };
  }, [isEnabled]);

  return {
    isEnabled,
    isLoaded,
    handDetected,
    palmPos,
    deltaPos,
    pinchDistance,
    gesture,
    landmarks,
    canvasRef,
    errorMessage,
    toggleHandTracking
  };
};

// Holographic Hand Skeleton Drawing Helper
function drawHolographicHand(canvas: HTMLCanvasElement, landmarks?: HandLandmark[]) {
  const ctx = canvas.getContext('2d');
  if (!ctx) return;

  ctx.clearRect(0, 0, canvas.width, canvas.height);

  if (!landmarks || landmarks.length === 0) return;

  const connections = [
    [0, 1], [1, 2], [2, 3], [3, 4], // Thumb
    [0, 5], [5, 6], [6, 7], [7, 8], // Index
    [5, 9], [9, 10], [10, 11], [11, 12], // Middle
    [9, 13], [13, 14], [14, 15], [15, 16], // Ring
    [13, 17], [17, 18], [18, 19], [19, 20], // Pinky
    [0, 17] // Palm Base
  ];

  // Draw Glowing Connections
  ctx.strokeStyle = '#00f0ff';
  ctx.lineWidth = 2;
  ctx.shadowColor = '#00f0ff';
  ctx.shadowBlur = 8;

  connections.forEach(([i, j]) => {
    const p1 = landmarks[i];
    const p2 = landmarks[j];

    ctx.beginPath();
    ctx.moveTo((1 - p1.x) * canvas.width, p1.y * canvas.height);
    ctx.lineTo((1 - p2.x) * canvas.width, p2.y * canvas.height);
    ctx.stroke();
  });

  // Draw Glowing Joint Nodes
  landmarks.forEach((p, idx) => {
    const x = (1 - p.x) * canvas.width;
    const y = p.y * canvas.height;

    ctx.fillStyle = idx === 4 || idx === 8 ? '#ffea00' : '#39ff14';
    ctx.shadowColor = idx === 4 || idx === 8 ? '#ffea00' : '#39ff14';
    ctx.shadowBlur = 10;

    ctx.beginPath();
    ctx.arc(x, y, idx === 8 ? 5 : 3, 0, 2 * Math.PI);
    ctx.fill();
  });
}
