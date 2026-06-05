import type { ReactElement } from "react";

import type { PublishMetadata, PublishPlatform } from "../../types/video";

interface MetadataFieldsProps {
  idPrefix: string;
  platform: PublishPlatform;
  metadata: PublishMetadata;
  /** Raw comma-separated tags input (parsed to `metadata.tags` by the parent). */
  tagsInput: string;
  disabled?: boolean;
  onPlatformChange: (platform: PublishPlatform) => void;
  onTitleChange: (title: string) => void;
  onDescriptionChange: (description: string) => void;
  onTagsInputChange: (tagsInput: string) => void;
}

const PLATFORM_OPTIONS: ReadonlyArray<{ id: PublishPlatform; label: string }> = [
  { id: "youtube_shorts", label: "YouTube Shorts" },
  { id: "instagram_reels", label: "Instagram Reels" },
];

/**
 * Shared target-platform + SEO metadata fields, reused by the publish and
 * schedule panels so the two stay in lockstep. Controlled inputs only — the
 * parent owns state and the (mockable) service call. snake_case `PublishMetadata`
 * mirrors the wire.
 */
export function MetadataFields({
  idPrefix,
  platform,
  metadata,
  tagsInput,
  disabled = false,
  onPlatformChange,
  onTitleChange,
  onDescriptionChange,
  onTagsInputChange,
}: MetadataFieldsProps): ReactElement {
  return (
    <div className="metadata-fields">
      <div className="metadata-field">
        <label className="metadata-field__label" htmlFor={`${idPrefix}-platform`}>
          Target platform
        </label>
        <select
          id={`${idPrefix}-platform`}
          className="metadata-field__input"
          value={platform}
          disabled={disabled}
          onChange={(event) =>
            onPlatformChange(event.target.value as PublishPlatform)
          }
        >
          {PLATFORM_OPTIONS.map((option) => (
            <option key={option.id} value={option.id}>
              {option.label}
            </option>
          ))}
        </select>
      </div>

      <div className="metadata-field">
        <label className="metadata-field__label" htmlFor={`${idPrefix}-title`}>
          SEO title
        </label>
        <input
          id={`${idPrefix}-title`}
          className="metadata-field__input"
          type="text"
          value={metadata.title}
          disabled={disabled}
          placeholder="e.g. The four-day week: does it actually work?"
          onChange={(event) => onTitleChange(event.target.value)}
        />
      </div>

      <div className="metadata-field">
        <label
          className="metadata-field__label"
          htmlFor={`${idPrefix}-description`}
        >
          Description
        </label>
        <textarea
          id={`${idPrefix}-description`}
          className="metadata-field__input metadata-field__input--area"
          value={metadata.description}
          disabled={disabled}
          rows={3}
          placeholder="Short description shown on the platform."
          onChange={(event) => onDescriptionChange(event.target.value)}
        />
      </div>

      <div className="metadata-field">
        <label className="metadata-field__label" htmlFor={`${idPrefix}-tags`}>
          Tags (comma-separated)
        </label>
        <input
          id={`${idPrefix}-tags`}
          className="metadata-field__input"
          type="text"
          value={tagsInput}
          disabled={disabled}
          placeholder="productivity, work, four-day-week"
          onChange={(event) => onTagsInputChange(event.target.value)}
        />
      </div>
    </div>
  );
}

/** Parses a comma-separated tags string into a trimmed, non-empty tag list. */
export function parseTags(tagsInput: string): string[] {
  return tagsInput
    .split(",")
    .map((tag) => tag.trim())
    .filter((tag) => tag.length > 0);
}
