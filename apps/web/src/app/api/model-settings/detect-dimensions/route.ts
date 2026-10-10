import { modelSettingsProxy } from "../../_shared/model-settings-proxy";
export const POST = (request: Request) => modelSettingsProxy(request, true);
