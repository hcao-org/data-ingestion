export const FIELD_NAMES = ['name', 'email', 'address', 'city', 'zip', 'phone', 'volunteerInterest']

/**
 * Assumption: OCR flags follow a "{field}_{reason}" convention (e.g. "phone_shape"
 * in the sample data). Row-level flags that don't match any known field prefix are
 * ignored here rather than crashing, since the real flag vocabulary isn't final yet.
 */
export function fieldFlags(flags, field) {
  return flags.filter((flag) => flag === field || flag.startsWith(`${field}_`))
}

export function flagLabel(flag) {
  return flag.replace(/_/g, ' ')
}
