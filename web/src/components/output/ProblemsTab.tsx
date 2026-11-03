export function ProblemsTab({ error }: { error: string }) {
  return error ? <div className="run-problem" role="alert">{error}</div> : <p className="output-empty">No se detectaron problemas.</p>;
}

