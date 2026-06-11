/** Build-time UI feature flags. Flip and rebuild to toggle. */

// In-app profile creation ("+ New profile" / "Create new") is disabled for now —
// profiles are created via the install_profile.sh setup script instead. Set to
// true to bring the create flow back.
export const ENABLE_PROFILE_CREATE = false;
