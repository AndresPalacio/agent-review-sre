import type { NodeProps } from "@xyflow/react";
import type { FlowNode } from "../../../types/flow";
import { NodeShell } from "./NodeShell";

export function PortfolioManagerNode(props: NodeProps<FlowNode>) { return <NodeShell {...props} />; }
