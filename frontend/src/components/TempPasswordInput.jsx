import { generateTempPassword } from "../utils/password";

/* Text input (visible: the admin passes it on) with a Generate button. */
export default function TempPasswordInput({ id, value, onChange }) {
  return (
    <div className="input-with-action">
      <input
        id={id}
        type="text"
        autoComplete="off"
        spellCheck={false}
        minLength={8}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        required
      />
      <button type="button" onClick={() => onChange(generateTempPassword())}>
        Generate
      </button>
    </div>
  );
}
