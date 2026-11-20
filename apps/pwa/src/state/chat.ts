/**
 * Chat screen store (pure helpers; React keeps the state).
 * The server seeds `messages` from the bootstrap; sending appends
 * optimistically and reconciles with the server echo; SSE frames
 * update the live work strip, never the message list.
 */
import type { ChatMessage, OfficeProjection, SseEvent } from "../api/client";
import { officeAgents } from "./office";

export interface ChatState {
  messages: ChatMessage[];
  sending: boolean;
  error: string | null;
  liveOffice: OfficeProjection | null;
  liveConnected: boolean;
}

export function initialChatState(messages: ChatMessage[] = []): ChatState {
  return { messages, sending: false, error: null, liveOffice: null, liveConnected: false };
}

/** Local echo for a message the user just sent (replaced on server reply). */
export function localEcho(text: string): ChatMessage {
  return { sender: "users", text, created_at: Date.now() / 1000 };
}

export function appendMessage(state: ChatState, message: ChatMessage): ChatState {
  return { ...state, messages: [...state.messages, message] };
}

/** Replace the newest local echo (same text, no server id) with the server echo. */
export function reconcileEcho(state: ChatState, serverMessage: ChatMessage): ChatState {
  const index = state.messages.findLastIndex(
    (m) => m.sender === "users" && m.text === serverMessage.text,
  );
  if (index === -1) return appendMessage(state, serverMessage);
  const messages = [...state.messages];
  messages[index] = serverMessage;
  return { ...state, messages };
}

/** Fold one SSE event into the live strip state. */
export function applySseEvent(state: ChatState, event: SseEvent): ChatState {
  if (event.type === "office" && event.content) {
    return { ...state, liveOffice: event.content, liveConnected: true };
  }
  if (event.type === "heartbeat") {
    return { ...state, liveConnected: true };
  }
  return state;
}

/** Agents currently working, from the latest office projection. */
export function workingAgents(office: OfficeProjection | null): string[] {
  return officeAgents(office)
    .filter((a) => a.status === "working")
    .map((a) => a.agent_id ?? "")
    .filter((id) => id.length > 0);
}
