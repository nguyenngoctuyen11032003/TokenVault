// Installer targets (#/installer): one row per package type the owner can create.
//   id          radio value / i18n key under installer.target.*
//   apiTarget   3rd argument of api.create_installer (null = legacy 2-argument Windows exe call)
//   hintKey     hint shown under the selector while this target is selected
//   instrKey    i18n prefix of the employee instructions (installer.<instrKey>.l1..l7)
//   noteKey     note above the instructions on the result screen
//   missingKey  message for a [template_missing] error of this target

export const TARGETS = Object.freeze([
  { id: 'windows-portable', apiTarget: 'windows-portable', hintKey: 'targetHint.windows-portable', instrKey: 'instrPortable', noteKey: 'portableNote', missingKey: 'templateMissingPortable' },
  { id: 'windows', apiTarget: null, hintKey: 'targetHint.windows', instrKey: 'instr', noteKey: 'smartscreenNote', missingKey: 'templateMissing' },
  { id: 'mac', apiTarget: 'mac', hintKey: 'targetHint.mac', instrKey: 'instrMac', noteKey: 'macNote', missingKey: 'templateMissingMac' },
]);

// The no-exe package is the default and first option: it runs on machines whose Smart App
// Control / Application Control blocks every unsigned .exe (and on all the others).
export const DEFAULT_TARGET = 'windows-portable';

export function targetById(id) {
  return TARGETS.find((x) => x.id === id) ?? TARGETS[0];
}
