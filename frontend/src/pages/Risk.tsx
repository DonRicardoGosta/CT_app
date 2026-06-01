// Risk & capital config CRUD (REQ-007).
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { endpoints, type RiskConfig } from "@/lib/api";
import { Button, Card, CardTitle, Empty, Field, Input, Select, Table, Td, Tr } from "@/components/ui";
import { usd } from "@/lib/format";

const BLANK = {
  name: "default",
  max_capital_usd: "100",
  max_loss_usd: "50",
  min_investment_usd: "1",
  base_leverage: 5,
  max_leverage: 20,
  leverage_step: 1,
  allow_hedge: true,
  fee_rate: "0.0006",
};

export default function Risk() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["riskConfigs"], queryFn: endpoints.riskConfigs });
  const [form, setForm] = useState<Record<string, unknown>>(BLANK);

  const create = useMutation({
    mutationFn: () => endpoints.createRiskConfig(form as Partial<RiskConfig>),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["riskConfigs"] }),
  });
  const remove = useMutation({
    mutationFn: (id: number) => endpoints.deleteRiskConfig(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["riskConfigs"] }),
  });

  const upd = (k: string, v: unknown) => setForm({ ...form, [k]: v });

  return (
    <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
      <Card className="lg:col-span-2">
        <CardTitle>Saved risk profiles</CardTitle>
        {!data?.length ? (
          <Empty>No risk profiles yet</Empty>
        ) : (
          <Table head={["Name", "Min inv.", "Max capital", "Max loss", "Lev", "Hedge", ""]}>
            {data.map((r) => (
              <Tr key={r.id}>
                <Td className="font-medium">{r.name}</Td>
                <Td className="num">{usd(r.min_investment_usd)}</Td>
                <Td className="num">{usd(r.max_capital_usd)}</Td>
                <Td className="num">{usd(r.max_loss_usd)}</Td>
                <Td className="num">
                  {r.base_leverage}–{r.max_leverage}x
                </Td>
                <Td>{r.allow_hedge ? "yes" : "no"}</Td>
                <Td>
                  <Button variant="ghost" onClick={() => remove.mutate(r.id)}>
                    Delete
                  </Button>
                </Td>
              </Tr>
            ))}
          </Table>
        )}
      </Card>

      <Card>
        <CardTitle>New risk profile</CardTitle>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Name">
            <Input value={String(form.name)} onChange={(e) => upd("name", e.target.value)} />
          </Field>
          <Field label="Min investment (USD)">
            <Input
              type="number"
              value={String(form.min_investment_usd)}
              onChange={(e) => upd("min_investment_usd", e.target.value)}
            />
          </Field>
          <Field label="Max capital (USD)">
            <Input
              type="number"
              value={String(form.max_capital_usd)}
              onChange={(e) => upd("max_capital_usd", e.target.value)}
            />
          </Field>
          <Field label="Max loss (USD)">
            <Input
              type="number"
              value={String(form.max_loss_usd)}
              onChange={(e) => upd("max_loss_usd", e.target.value)}
            />
          </Field>
          <Field label="Base leverage (x)">
            <Input
              type="number"
              value={String(form.base_leverage)}
              onChange={(e) => upd("base_leverage", Number(e.target.value))}
            />
          </Field>
          <Field label="Max leverage (x)">
            <Input
              type="number"
              value={String(form.max_leverage)}
              onChange={(e) => upd("max_leverage", Number(e.target.value))}
            />
          </Field>
          <Field label="Leverage step">
            <Input
              type="number"
              value={String(form.leverage_step)}
              onChange={(e) => upd("leverage_step", Number(e.target.value))}
            />
          </Field>
          <Field label="Allow hedge">
            <Select
              value={String(form.allow_hedge)}
              onChange={(e) => upd("allow_hedge", e.target.value === "true")}
            >
              <option value="true">true</option>
              <option value="false">false</option>
            </Select>
          </Field>
        </div>
        <div className="mt-4">
          <Button variant="primary" onClick={() => create.mutate()}>
            Save profile
          </Button>
        </div>
      </Card>
    </div>
  );
}
