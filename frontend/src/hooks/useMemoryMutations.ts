import { useMemoryStore } from "@/stores/memoryStore";
import { apiClient } from "@/api/client";

export function useMemoryMutations() {
  const pending = useMemoryStore((s) => s.pending);
  const setPending = useMemoryStore((s) => s.setPending);
  const updatePendingText = useMemoryStore((s) => s.updatePendingText);
  const removePending = useMemoryStore((s) => s.removePending);
  const removeApproved = useMemoryStore((s) => s.removeApproved);

  const approveAll = async () => {
    const ids = pending.map((m) => m.id);
    const snapshot = [...pending];
    setPending([]);
    try {
      await apiClient.post("/v1/memory/approve", { memory_ids: ids });
    } catch {
      setPending(snapshot);
    }
  };

  const approveOne = async (memoryId: string) => {
    const snapshot = [...pending];
    removePending([memoryId]);
    try {
      await apiClient.post("/v1/memory/approve", { memory_ids: [memoryId] });
    } catch {
      setPending(snapshot);
    }
  };

  const rejectAll = async () => {
    const ids = pending.map((m) => m.id);
    const snapshot = [...pending];
    setPending([]);
    try {
      await apiClient.post("/v1/memory/reject", { memory_ids: ids });
    } catch {
      setPending(snapshot);
    }
  };

  const rejectOne = async (memoryId: string) => {
    removePending([memoryId]);
    try {
      await apiClient.post("/v1/memory/reject", { memory_ids: [memoryId] });
    } catch {
      // already removed optimistically
    }
  };

  const editPending = async (memoryId: string, newText: string) => {
    updatePendingText(memoryId, newText);
    try {
      await apiClient.patch(`/v1/memory/${memoryId}`, { text: newText });
    } catch {
      // text update is local-only if API fails
    }
  };

  const deleteMemory = async (memoryId: string) => {
    removeApproved(memoryId);
    try {
      await apiClient.delete(`/v1/memory/${memoryId}`);
    } catch {
      // already removed optimistically
    }
  };

  return { approveAll, approveOne, rejectAll, rejectOne, editPending, deleteMemory };
}
