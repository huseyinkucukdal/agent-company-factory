"use client";

import { Conversations } from "@/components/conversations";
import { LiveFeed } from "@/components/live-feed";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useEventStream } from "@/lib/sse";
import type { Agent } from "@/lib/types";

interface MonitorPanelProps {
  companyId: string;
  agents?: Agent[];
}

/**
 * Owns the per-company SSE subscription and shares it across the
 * monitoring tabs. Keeping the stream here means the Live Feed and the
 * Conversations view see the exact same event sequence (and we open
 * only one EventSource per page load).
 */
export function MonitorPanel({ companyId, agents = [] }: MonitorPanelProps) {
  const { events, connected } = useEventStream(
    `/api/companies/${companyId}/stream`,
    { bufferSize: 2_000 },
  );

  return (
    <Tabs defaultValue="live" className="w-full">
      <TabsList>
        <TabsTrigger value="live">Live feed</TabsTrigger>
        <TabsTrigger value="threads">Conversations</TabsTrigger>
      </TabsList>
      <TabsContent value="live">
        <LiveFeed events={events} connected={connected} agents={agents} />
      </TabsContent>
      <TabsContent value="threads">
        <Conversations events={events} agents={agents} />
      </TabsContent>
    </Tabs>
  );
}
