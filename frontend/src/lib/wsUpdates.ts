import type { Message, TaskRun, WSMessage } from './types';

export function applyAssistantProgress(messages: Message[], event: WSMessage): Message[] {
  const messageId = event.message_id;
  if (!messageId || !event.content) return messages;
  return messages.map(message => (
    message.id === messageId ? { ...message, content: event.content || message.content } : message
  ));
}

export function applyTaskUpdated(tasks: TaskRun[], event: WSMessage): TaskRun[] {
  if (!event.run_id || !event.task_title || !event.task_summary || !event.task_source) {
    return tasks;
  }
  return tasks.map(task => task.run_id === event.run_id ? {
    ...task,
    task_title: event.task_title as string,
    task_summary: event.task_summary as string,
    task_source: event.task_source as string,
    updated_at: event.updated_at ?? task.updated_at,
  } : task);
}
