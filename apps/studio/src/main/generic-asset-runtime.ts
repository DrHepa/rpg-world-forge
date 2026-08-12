import { validateGenericAssetContract } from "./generic-asset-contracts";
import {
  validateGenericAssetpack,
  verifyGenericAssetpackDirectory,
} from "./generic-assetpack";

export const GENERIC_ASSET_VALIDATOR_RUNTIME = Object.freeze({
  contract_formats: Object.freeze([
    "world-forge.asset_inventory",
    "world-forge.asset_license_record",
    "world-forge.asset_manifest",
    "world-forge.asset_processing_receipt",
    "world-forge.asset_processing_recipe",
    "world-forge.asset_production_receipt",
    "world-forge.asset_production_request",
    "world-forge.asset_provenance_record",
    "world-forge.asset_qa_report",
    "world-forge.asset_selection",
    "world-forge.asset_spec",
    "world-forge.asset_style",
    "world-forge.asset_subject",
    "world-forge.asset_target",
  ]),
  sealed_pack_formats: Object.freeze([
    "world-forge.assetpack",
  ]),
  format: "world-forge.studio_internal_generic_asset_validator",
  format_version: 2,
} as const);

export {
  validateGenericAssetContract,
  validateGenericAssetpack,
  verifyGenericAssetpackDirectory,
};
