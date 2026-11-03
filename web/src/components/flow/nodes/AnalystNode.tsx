import type { NodeProps } from "@xyflow/react";
import type { FlowNode } from "../../../types/flow";
import { NodeShell } from "./NodeShell";

export function AnalystNode(props: NodeProps<FlowNode>) { return <NodeShell {...props} />; }
