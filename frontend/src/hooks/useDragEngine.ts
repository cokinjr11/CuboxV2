import { useCallback, useEffect, useRef, useState } from "react";
import { useThree } from "@react-three/fiber";
import * as THREE from "three";
import { MOVEMENT_STEP_MM, SCENE_SCALE, SNAP_TOLERANCE_MM } from "../config";
import { findRestingZ, type Box } from "../geometry/geometry";
import type { PackingResult } from "../types";

const CLICK_THRESHOLD_PX = 4;

export type DragMode = "move" | "insert";

interface DragCandidate {
  x: number;
  y: number;
  z: number;
  valid: boolean;
}

export interface ActiveDrag {
  mode: DragMode;
  pieceId: string;
  dx: number;
  dy: number;
  dz: number;
  stackable: boolean;
  grabOffsetX: number;
  grabOffsetY: number;
  startClientX: number;
  startClientY: number;
  moved: boolean;
  candidate: DragCandidate;
}

interface Props {
  result: PackingResult | null;
  onCommitMove: (
    pieceId: string,
    x: number,
    y: number,
    z: number,
    dx: number,
    dy: number,
    dz: number
  ) => Promise<void>;
  onCommitInsert: (
    unloadedId: string,
    x: number,
    y: number,
    z: number,
    dx: number,
    dy: number,
    dz: number
  ) => Promise<void>;
}

/**
 * Motor de arrastre para mover piezas existentes o "recoger" el fantasma de
 * insercion de una pieza de Unloaded Items. Solo hace validacion geometrica
 * en el frontend (geometry.ts) para dar feedback instantaneo: la confirmacion
 * real ocurre en el backend al soltar (onCommitMove / onCommitInsert), que es
 * la unica fuente de verdad.
 */
export function useDragEngine({ result, onCommitMove, onCommitInsert }: Props) {
  const { camera, gl, raycaster } = useThree();
  const [drag, setDrag] = useState<ActiveDrag | null>(null);
  const othersRef = useRef<Box[]>([]);
  const floorPlane = useRef(new THREE.Plane(new THREE.Vector3(0, 1, 0), 0));
  const ndc = useRef(new THREE.Vector2());
  const hitPoint = useRef(new THREE.Vector3());

  const container = result?.container ?? null;

  const pointerToFloor = useCallback(
    (clientX: number, clientY: number): { x: number; y: number } | null => {
      if (!container) return null;
      const rect = gl.domElement.getBoundingClientRect();
      ndc.current.x = ((clientX - rect.left) / rect.width) * 2 - 1;
      ndc.current.y = -((clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(ndc.current, camera);
      const hit = raycaster.ray.intersectPlane(floorPlane.current, hitPoint.current);
      if (!hit) return null;
      return {
        x: hitPoint.current.x / SCENE_SCALE + container.length / 2,
        y: hitPoint.current.z / SCENE_SCALE + container.width / 2,
      };
    },
    [container, camera, gl, raycaster]
  );

  const snapAxis = (raw: number, size: number, containerSize: number, edges: number[]): number => {
    const stepped = Math.round(raw / MOVEMENT_STEP_MM) * MOVEMENT_STEP_MM;
    let best = stepped;
    let bestDist = SNAP_TOLERANCE_MM;
    for (const edge of edges) {
      const d = Math.abs(stepped - edge);
      if (d <= bestDist) {
        best = edge;
        bestDist = d;
      }
    }
    return Math.max(0, Math.min(best, Math.max(0, containerSize - size)));
  };

  const computeCandidate = useCallback(
    (
      pointerX: number,
      pointerY: number,
      grabOffsetX: number,
      grabOffsetY: number,
      dx: number,
      dy: number,
      dz: number,
      stackable: boolean,
      pieceId: string
    ): DragCandidate => {
      if (!container) return { x: 0, y: 0, z: 0, valid: false };
      const others = othersRef.current;

      const edgesX = others.flatMap((o) => [o.x, o.x + o.dx, o.x - dx, o.x + o.dx - dx]);
      const edgesY = others.flatMap((o) => [o.y, o.y + o.dy, o.y - dy, o.y + o.dy - dy]);

      const x = snapAxis(pointerX - grabOffsetX, dx, container.length, edgesX);
      const y = snapAxis(pointerY - grabOffsetY, dy, container.width, edgesY);

      return findRestingZ(x, y, dx, dy, dz, stackable, pieceId, others, container);
    },
    [container]
  );

  /** Empieza a arrastrar: una pieza ya colocada (mode="move") o el fantasma
   * de insercion recien "recogido" (mode="insert"). currentX/Y/Z es la
   * posicion actual de la pieza (donde esta parada antes de agarrarla). */
  const beginDrag = useCallback(
    (
      mode: DragMode,
      pieceId: string,
      currentX: number,
      currentY: number,
      currentZ: number,
      dx: number,
      dy: number,
      dz: number,
      stackable: boolean,
      clientX: number,
      clientY: number
    ) => {
      if (!result) return;
      const pointer = pointerToFloor(clientX, clientY);
      if (!pointer) return;

      othersRef.current = result.placed
        .filter((p) => p.id !== pieceId)
        .map((p) => ({ id: p.id, x: p.x, y: p.y, z: p.z, dx: p.dx, dy: p.dy, dz: p.dz, stackable: p.stackable }));

      setDrag({
        mode,
        pieceId,
        dx,
        dy,
        dz,
        stackable,
        grabOffsetX: pointer.x - currentX,
        grabOffsetY: pointer.y - currentY,
        startClientX: clientX,
        startClientY: clientY,
        moved: false,
        candidate: { x: currentX, y: currentY, z: currentZ, valid: true },
      });
    },
    [result, pointerToFloor]
  );

  const updatePointer = useCallback(
    (clientX: number, clientY: number) => {
      setDrag((current) => {
        if (!current) return current;
        const pointer = pointerToFloor(clientX, clientY);
        if (!pointer) return current;

        const movedPx = Math.hypot(clientX - current.startClientX, clientY - current.startClientY);
        const moved = current.moved || movedPx > CLICK_THRESHOLD_PX;

        const candidate = computeCandidate(
          pointer.x,
          pointer.y,
          current.grabOffsetX,
          current.grabOffsetY,
          current.dx,
          current.dy,
          current.dz,
          current.stackable,
          current.pieceId
        );
        return { ...current, moved, candidate };
      });
    },
    [pointerToFloor, computeCandidate]
  );

  const endDrag = useCallback(() => {
    setDrag((current) => {
      if (!current) return null;
      if (current.moved && current.candidate.valid) {
        const { mode, pieceId, candidate, dx, dy, dz } = current;
        const commit = mode === "move" ? onCommitMove : onCommitInsert;
        commit(pieceId, candidate.x, candidate.y, candidate.z, dx, dy, dz).catch(() => {
          /* el backend es la autoridad final; si rechaza, la UI vuelve al ultimo estado confirmado */
        });
      }
      return null;
    });
  }, [onCommitMove, onCommitInsert]);

  const cancelDrag = useCallback(() => setDrag(null), []);

  useEffect(() => {
    if (!drag) return;
    const handleMove = (e: PointerEvent) => updatePointer(e.clientX, e.clientY);
    const handleUp = () => endDrag();
    window.addEventListener("pointermove", handleMove);
    window.addEventListener("pointerup", handleUp);
    return () => {
      window.removeEventListener("pointermove", handleMove);
      window.removeEventListener("pointerup", handleUp);
    };
  }, [drag !== null, updatePointer, endDrag]);

  return { drag, beginDrag, cancelDrag };
}
