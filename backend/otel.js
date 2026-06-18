import { NodeTracerProvider } from '@opentelemetry/sdk-trace-node';
const provider = new NodeTracerProvider();   // no exporter on purpose
provider.register();                          // OneAgent captures emitted spans
export {};
