// Settings: API keys (encrypted at rest) and connection test. Everything that is
// trading-related is configured here, never via environment variables (REQ-009).
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { endpoints } from "@/lib/api";
import { Badge, Button, Card, CardTitle, Empty, Field, Input, Table, Td, Tr } from "@/components/ui";
import { time } from "@/lib/format";

export default function Settings() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["apiKeys"], queryFn: endpoints.apiKeys });
  const [form, setForm] = useState({ name: "", exchange: "bitunix", api_key: "", secret: "" });
  const [testResult, setTestResult] = useState<Record<number, string>>({});

  const create = useMutation({
    mutationFn: () => endpoints.createApiKey(form),
    onSuccess: () => {
      setForm({ name: "", exchange: "bitunix", api_key: "", secret: "" });
      qc.invalidateQueries({ queryKey: ["apiKeys"] });
    },
  });
  const remove = useMutation({
    mutationFn: (id: number) => endpoints.deleteApiKey(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["apiKeys"] }),
  });

  async function test(id: number) {
    setTestResult({ ...testResult, [id]: "testing…" });
    try {
      const res = await endpoints.testApiKey(id);
      setTestResult({ ...testResult, [id]: res.ok ? "ok" : "failed" });
    } catch (e) {
      setTestResult({ ...testResult, [id]: `error: ${String(e)}` });
    }
  }

  return (
    <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
      <Card className="lg:col-span-2">
        <CardTitle>Bitunix API keys</CardTitle>
        {!data?.length ? (
          <Empty>No API keys yet — add one to enable live trading</Empty>
        ) : (
          <Table head={["Name", "Exchange", "Key", "Active", "Created", "", ""]}>
            {data.map((k) => (
              <Tr key={k.id}>
                <Td className="font-medium">{k.name}</Td>
                <Td>{k.exchange}</Td>
                <Td className="num text-muted">{k.api_key_masked}</Td>
                <Td>{k.is_active ? <Badge tone="up">active</Badge> : <Badge>off</Badge>}</Td>
                <Td className="text-muted">{time(k.created_at)}</Td>
                <Td>
                  <Button variant="ghost" onClick={() => test(k.id)}>
                    Test {testResult[k.id] ? `(${testResult[k.id]})` : ""}
                  </Button>
                </Td>
                <Td>
                  <Button variant="ghost" onClick={() => remove.mutate(k.id)}>
                    Delete
                  </Button>
                </Td>
              </Tr>
            ))}
          </Table>
        )}
      </Card>

      <Card>
        <CardTitle>Add API key</CardTitle>
        <div className="space-y-3">
          <Field label="Name">
            <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </Field>
          <Field label="API key">
            <Input
              value={form.api_key}
              onChange={(e) => setForm({ ...form, api_key: e.target.value })}
            />
          </Field>
          <Field label="API secret">
            <Input
              type="password"
              value={form.secret}
              onChange={(e) => setForm({ ...form, secret: e.target.value })}
            />
          </Field>
          <Button variant="primary" onClick={() => create.mutate()} disabled={!form.name || !form.api_key}>
            Save key
          </Button>
          <p className="text-xs text-muted">
            The secret is encrypted at rest and never returned by the API — only a
            masked form and a connection-test action.
          </p>
        </div>
      </Card>
    </div>
  );
}
