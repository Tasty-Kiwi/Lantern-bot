import { DiscordClient, DiscordError, isColorRole, type Message } from './discord.ts';

export interface ColorCommand {
  application: string;
  token: string;
  guild: string;
  member: string;
  roles: string[];
  color?: string;
}

export async function runColor(command: ColorCommand, botToken: string): Promise<void> {
  const api = new DiscordClient(botToken);
  let message: Message;
  try {
    const roles = await api.roles(command.guild);
    const oldRoles = roles.filter((role) => command.roles.includes(role.id) && isColorRole(role, command.guild));
    let target;
    if (command.color !== undefined) {
      target = roles.find((role) => isColorRole(role, command.guild)
        && role.name.toLowerCase() === command.color
        && (role.colors?.primary_color ?? role.color) === parseInt(command.color!, 16));
      target ??= await api.createRole(command.guild, command.color);
      // Add first: creation/assignment errors must leave existing colors intact.
      if (!command.roles.includes(target.id)) {
        await api.memberRole('PUT', command.guild, command.member, target.id);
      }
    }
    for (const role of oldRoles) {
      if (role.id === target?.id) continue;
      try {
        await api.memberRole('DELETE', command.guild, command.member, role.id);
      } catch (error) {
        // A concurrently deleted role/member is already absent.
        if (!(error instanceof DiscordError && error.status === 404)) throw error;
      }
    }
    message = { embeds: [{
      title: 'Color changer',
      description: command.color !== undefined ? `Color has been changed to \`${command.color}\`.`
        : oldRoles.length ? 'Color was removed.' : 'You do not have any colors selected!',
      color: command.color !== undefined ? parseInt(command.color, 16) : 0x6b003b,
    }] };
  } catch (error) {
    console.error(JSON.stringify({ event: 'color_command_failed',
      status: error instanceof DiscordError ? error.status : undefined,
      code: error instanceof DiscordError ? error.code : undefined }));
    message = { content: error instanceof DiscordError && error.status === 403
      ? 'I could not update your color. Check that Lantern has Manage Roles and its highest role is above the color roles. Your existing color may still be present; try again after fixing permissions.'
      : 'I could not finish updating your color. Your existing color may still be present. Please try again shortly.' };
  }
  // A separate budget leaves time to resolve the deferred reply after a timeout.
  try {
    await new DiscordClient(botToken, Date.now() + 4_000).editReply(command.application, command.token, message);
  } catch (error) {
    console.error(JSON.stringify({ event: 'interaction_reply_failed',
      status: error instanceof DiscordError ? error.status : undefined }));
  }
}
