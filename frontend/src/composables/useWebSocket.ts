import { onUnmounted, ref } from 'vue';

export interface WebSocketMessage {
  type: string
  [key: string]: any
}

export function useWebSocket(onMessage: (msg: WebSocketMessage) => void) {
  const ws = ref<WebSocket | null>(null);
  const wsConnected = ref(false);
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let wsReconnectDelay = 3000;
  const MAX_RECONNECT_DELAY = 60000;

  function connect() {
    if (ws.value) return;
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const basePath = (import.meta.env.BASE_URL || '/').replace(/\/+$/, '');
    const wsUrl = `${protocol}//${window.location.host}${basePath || ''}/ws`;

    try {
      const socket = new WebSocket(wsUrl);
      socket.onopen = () => {
        wsConnected.value = true;
        wsReconnectDelay = 3000;
      };
      socket.onmessage = (event) => {
        try {
          const data: WebSocketMessage = JSON.parse(event.data);
          if (data.type === 'ping') {
            socket.send(JSON.stringify({ type: 'pong' }));
            return;
          }
          onMessage(data);
        } catch {
          // ignore non-json payloads
        }
      };
      socket.onclose = () => {
        wsConnected.value = false;
        ws.value = null;
        reconnectTimer = setTimeout(connect, wsReconnectDelay);
        wsReconnectDelay = Math.min(wsReconnectDelay * 2, MAX_RECONNECT_DELAY);
      };
      socket.onerror = () => {
        wsConnected.value = false;
      };
      ws.value = socket;
    } catch {
      reconnectTimer = setTimeout(connect, wsReconnectDelay);
      wsReconnectDelay = Math.min(wsReconnectDelay * 2, MAX_RECONNECT_DELAY);
    }
  }

  function disconnect() {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (ws.value) {
      ws.value.onclose = null;
      ws.value.close();
      ws.value = null;
    }
    wsConnected.value = false;
  }

  onUnmounted(disconnect);

  return {
    ws,
    wsConnected,
    connect,
    disconnect,
  };
}
