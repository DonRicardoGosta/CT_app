// Auto-generates a parameter form from a backend-provided JSON schema (REQ-005/008).
// Adding a new strategy needs NO new UI — its schema drives this form.
import type { JsonSchema, JsonSchemaProp } from "@/lib/api";
import { Field, Input, Select } from "@/components/ui";

export type FormValue = Record<string, unknown>;

export function defaultsFromSchema(schema: JsonSchema): FormValue {
  const out: FormValue = {};
  for (const [key, prop] of Object.entries(schema.properties ?? {})) {
    if (prop.default !== undefined) out[key] = prop.default;
  }
  return out;
}

function isNumeric(prop: JsonSchemaProp): boolean {
  return prop.type === "number" || prop.type === "integer";
}

export default function SchemaForm({
  schema,
  value,
  onChange,
}: {
  schema: JsonSchema;
  value: FormValue;
  onChange: (v: FormValue) => void;
}) {
  const props = schema.properties ?? {};
  const set = (key: string, v: unknown) => onChange({ ...value, [key]: v });

  return (
    <div className="grid grid-cols-2 gap-3">
      {Object.entries(props).map(([key, prop]) => {
        const label = prop.title || key;
        const current = value[key] ?? prop.default ?? "";
        if (prop.type === "boolean") {
          return (
            <Field key={key} label={label}>
              <Select
                value={String(current)}
                onChange={(e) => set(key, e.target.value === "true")}
              >
                <option value="true">true</option>
                <option value="false">false</option>
              </Select>
            </Field>
          );
        }
        return (
          <Field key={key} label={label}>
            <Input
              type={isNumeric(prop) ? "number" : "text"}
              step="any"
              value={String(current)}
              min={prop.minimum}
              max={prop.maximum}
              title={prop.description}
              onChange={(e) =>
                set(key, isNumeric(prop) ? e.target.value : e.target.value)
              }
            />
          </Field>
        );
      })}
    </div>
  );
}
