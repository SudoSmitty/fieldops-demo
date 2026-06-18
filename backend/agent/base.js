// event names: response.status, response.thinking, response.tool_use,
// response.tool_result, response.table, response.text.delta, response, error
export const EVENTS = Object.freeze({
  STATUS:'response.status', THINKING:'response.thinking', TOOL_USE:'response.tool_use',
  TOOL_RESULT:'response.tool_result', TABLE:'response.table',
  TEXT_DELTA:'response.text.delta', DONE:'response', ERROR:'error'
});
