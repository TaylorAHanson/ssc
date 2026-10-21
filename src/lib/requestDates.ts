import { formatInTimeZone } from 'date-fns-tz';

/** Request and approval timestamps are stored as naive UTC; read them as UTC. */
export const parseUtcDate = (dateString: string) => {
  if (!dateString) return new Date();
  // If string doesn't end with Z and doesn't have timezone offset, assume UTC and append Z
  if (!dateString.endsWith('Z') && !/[+-]\d{2}:?\d{2}$/.test(dateString)) {
    return new Date(dateString + 'Z');
  }
  return new Date(dateString);
};

export const formatDate = (dateString: string) => {
  if (!dateString) return '';
  return formatInTimeZone(parseUtcDate(dateString), 'America/Los_Angeles', 'MMM d, yyyy h:mm a zzz');
};
