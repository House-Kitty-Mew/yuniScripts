"""
shm_database.py — Database schema for Simulated Health Mechanics extension.

All tables prefixed with ext_shm_ to namespace from core AH and SP tables.
Schema creates on load; safe to call repeatedly.
"""

from datetime import datetime, timezone
from typing import Optional

from AUCTIONHOUSE.ah_database import get_db
from AUCTIONHOUSE.ah_logger import get_logger

log = get_logger()

# ══════════════════════════════════════════════════════════════════════
# Schema Definitions
# ══════════════════════════════════════════════════════════════════════

_BLOOD_TABLE = """
CREATE TABLE IF NOT EXISTS ext_shm_blood (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid      TEXT NOT NULL UNIQUE,
    blood_volume_ml   REAL NOT NULL DEFAULT 5000.0,
    max_blood_volume  REAL NOT NULL DEFAULT 5000.0,
    blood_toxicity    REAL NOT NULL DEFAULT 0.0,
    blood_type        TEXT NOT NULL DEFAULT 'O+',
    oxygen_saturation REAL NOT NULL DEFAULT 98.0,
    red_blood_cells   REAL NOT NULL DEFAULT 85.0,
    white_blood_cells REAL NOT NULL DEFAULT 80.0,
    platelets         REAL NOT NULL DEFAULT 75.0,
    glucose_mg_dl     REAL NOT NULL DEFAULT 90.0,
    hemoglobin_g_dl   REAL NOT NULL DEFAULT 15.0,
    circulation_efficiency REAL NOT NULL DEFAULT 1.0
);
CREATE INDEX IF NOT EXISTS idx_shm_blood_persona ON ext_shm_blood(persona_uuid);
"""

_BONES_TABLE = """
CREATE TABLE IF NOT EXISTS ext_shm_anatomy_bones (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid      TEXT NOT NULL,
    bone_name         TEXT NOT NULL,
    bone_group        TEXT NOT NULL,
    location          TEXT NOT NULL,
    fractured         INTEGER NOT NULL DEFAULT 0,
    healing_progress  REAL NOT NULL DEFAULT 0.0,
    pain_contribution REAL NOT NULL DEFAULT 0.0,
    UNIQUE(persona_uuid, bone_name)
);
CREATE INDEX IF NOT EXISTS idx_shm_bones_persona ON ext_shm_anatomy_bones(persona_uuid);
CREATE INDEX IF NOT EXISTS idx_shm_bones_fractured ON ext_shm_anatomy_bones(fractured);
"""

_ORGANS_TABLE = """
CREATE TABLE IF NOT EXISTS ext_shm_anatomy_organs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid      TEXT NOT NULL,
    organ_name        TEXT NOT NULL,
    health            REAL NOT NULL DEFAULT 100.0,
    is_damaged        INTEGER NOT NULL DEFAULT 0,
    damage_type       TEXT,
    healing_progress  REAL NOT NULL DEFAULT 0.0,
    UNIQUE(persona_uuid, organ_name)
);
CREATE INDEX IF NOT EXISTS idx_shm_organs_persona ON ext_shm_anatomy_organs(persona_uuid);
"""

_MUSCLES_TABLE = """
CREATE TABLE IF NOT EXISTS ext_shm_muscles (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid      TEXT NOT NULL,
    muscle_group      TEXT NOT NULL,
    strength          REAL NOT NULL DEFAULT 30.0,
    max_strength      REAL NOT NULL DEFAULT 100.0,
    protein_level     REAL NOT NULL DEFAULT 80.0,
    fatigue           REAL NOT NULL DEFAULT 0.0,
    is_injured        INTEGER NOT NULL DEFAULT 0,
    injury_penalty    REAL NOT NULL DEFAULT 0.0,
    atrophy_days      INTEGER NOT NULL DEFAULT 0,
    UNIQUE(persona_uuid, muscle_group)
);
CREATE INDEX IF NOT EXISTS idx_shm_muscles_persona ON ext_shm_muscles(persona_uuid);
"""

_GENETICS_TABLE = """
CREATE TABLE IF NOT EXISTS ext_shm_genetics (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid      TEXT NOT NULL UNIQUE,
    metabolic_rate    REAL NOT NULL DEFAULT 1.0,
    immune_potency    REAL NOT NULL DEFAULT 1.0,
    pain_tolerance    REAL NOT NULL DEFAULT 1.0,
    healing_factor    REAL NOT NULL DEFAULT 1.0,
    disease_resistance REAL NOT NULL DEFAULT 1.0,
    muscle_growth_rate REAL NOT NULL DEFAULT 1.0,
    toxin_resistance  REAL NOT NULL DEFAULT 1.0,
    blood_efficiency  REAL NOT NULL DEFAULT 1.0,
    organ_vitality    REAL NOT NULL DEFAULT 1.0,
    nerve_density     REAL NOT NULL DEFAULT 1.0
);
CREATE INDEX IF NOT EXISTS idx_shm_genetics_persona ON ext_shm_genetics(persona_uuid);
"""

_DISEASES_TABLE = """
CREATE TABLE IF NOT EXISTS ext_shm_diseases (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid      TEXT NOT NULL,
    disease_name      TEXT NOT NULL,
    disease_type      TEXT NOT NULL,
    severity          INTEGER NOT NULL DEFAULT 1,
    stage             TEXT NOT NULL DEFAULT 'incubation',
    pathogen_count    REAL NOT NULL DEFAULT 0.0,
    virulence         REAL NOT NULL DEFAULT 1.0,
    incubation_ticks  INTEGER NOT NULL DEFAULT 0,
    total_incubation  INTEGER NOT NULL DEFAULT 10,
    duration_ticks    INTEGER NOT NULL DEFAULT 0,
    total_duration    INTEGER NOT NULL DEFAULT 48,
    is_contagious     INTEGER NOT NULL DEFAULT 0,
    transmission_mode TEXT DEFAULT 'contact',
    source            TEXT,
    immunity_ticks    INTEGER NOT NULL DEFAULT 0,
    is_active         INTEGER NOT NULL DEFAULT 1,
    started_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_shm_disease_persona ON ext_shm_diseases(persona_uuid);
CREATE INDEX IF NOT EXISTS idx_shm_disease_active ON ext_shm_diseases(is_active);
"""

_DISEASE_SPREAD_TABLE = """
CREATE TABLE IF NOT EXISTS ext_shm_disease_spread (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    source_persona    TEXT NOT NULL,
    target_persona    TEXT NOT NULL,
    disease_name      TEXT NOT NULL,
    transmission_type TEXT NOT NULL,
    exposure_time     TEXT NOT NULL,
    proximity_score   REAL NOT NULL DEFAULT 0.5,
    infection_succeeded INTEGER DEFAULT 0,
    UNIQUE(source_persona, target_persona, disease_name, exposure_time)
);
CREATE INDEX IF NOT EXISTS idx_shm_spread_source ON ext_shm_disease_spread(source_persona);
CREATE INDEX IF NOT EXISTS idx_shm_spread_target ON ext_shm_disease_spread(target_persona);
"""

_HYGIENE_TABLE = """
CREATE TABLE IF NOT EXISTS ext_shm_hygiene (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid      TEXT NOT NULL UNIQUE,
    personal_cleanliness REAL NOT NULL DEFAULT 70.0,
    oral_hygiene      REAL NOT NULL DEFAULT 60.0,
    clothing_cleanliness REAL NOT NULL DEFAULT 65.0,
    wound_care        REAL NOT NULL DEFAULT 100.0,
    environment_cleanliness REAL NOT NULL DEFAULT 60.0,
    last_washed_tick  INTEGER NOT NULL DEFAULT 0,
    last_dental_tick  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_shm_hygiene_persona ON ext_shm_hygiene(persona_uuid);
"""

_IMMUNE_TABLE = """
CREATE TABLE IF NOT EXISTS ext_shm_immune_response (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid      TEXT NOT NULL,
    target_type       TEXT NOT NULL,
    target_id         TEXT NOT NULL,
    immune_strength   REAL NOT NULL DEFAULT 50.0,
    inflammation      REAL NOT NULL DEFAULT 0.0,
    fever             REAL NOT NULL DEFAULT 0.0,
    wbc_production    REAL NOT NULL DEFAULT 0.0,
    battle_tick       INTEGER NOT NULL DEFAULT 0,
    is_active         INTEGER NOT NULL DEFAULT 1,
    is_autoimmune     INTEGER NOT NULL DEFAULT 0,
    resolved_at       TEXT,
    UNIQUE(persona_uuid, target_type, target_id)
);
CREATE INDEX IF NOT EXISTS idx_shm_immune_persona ON ext_shm_immune_response(persona_uuid);
CREATE INDEX IF NOT EXISTS idx_shm_immune_active ON ext_shm_immune_response(is_active);
"""

_PAIN_TABLE = """
CREATE TABLE IF NOT EXISTS ext_shm_pain (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid      TEXT NOT NULL,
    pain_source       TEXT NOT NULL,
    source_type       TEXT NOT NULL,
    pain_level        REAL NOT NULL DEFAULT 0.0,
    max_pain          REAL NOT NULL DEFAULT 0.0,
    duration_ticks    INTEGER NOT NULL DEFAULT 0,
    is_active         INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT NOT NULL,
    UNIQUE(persona_uuid, pain_source)
);
CREATE INDEX IF NOT EXISTS idx_shm_pain_persona ON ext_shm_pain(persona_uuid);
CREATE INDEX IF NOT EXISTS idx_shm_pain_active ON ext_shm_pain(is_active);
"""

_COMBAT_SKILLS_TABLE = """
CREATE TABLE IF NOT EXISTS ext_shm_combat_skills (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid      TEXT NOT NULL,
    skill_name        TEXT NOT NULL,
    level             REAL NOT NULL DEFAULT 1.0,
    proficiency_title TEXT NOT NULL DEFAULT 'Unskilled',
    xp_current        REAL NOT NULL DEFAULT 0.0,
    xp_to_next        REAL NOT NULL DEFAULT 10.0,
    times_used        INTEGER NOT NULL DEFAULT 0,
    last_used_tick    INTEGER NOT NULL DEFAULT 0,
    decay_counter     INTEGER NOT NULL DEFAULT 0,
    UNIQUE(persona_uuid, skill_name)
);
CREATE INDEX IF NOT EXISTS idx_shm_skills_persona ON ext_shm_combat_skills(persona_uuid);
"""

_NEGATIVE_TRAITS_TABLE = """
CREATE TABLE IF NOT EXISTS ext_shm_negative_traits (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid      TEXT NOT NULL,
    trait_name        TEXT NOT NULL,
    severity          REAL NOT NULL DEFAULT 1.0,
    cause             TEXT,
    is_active         INTEGER NOT NULL DEFAULT 1,
    acquired_tick     INTEGER NOT NULL DEFAULT 0,
    UNIQUE(persona_uuid, trait_name)
);
CREATE INDEX IF NOT EXISTS idx_shm_traits_persona ON ext_shm_negative_traits(persona_uuid);
CREATE INDEX IF NOT EXISTS idx_shm_traits_active ON ext_shm_negative_traits(is_active);
"""

_HEALING_LOG_TABLE = """
CREATE TABLE IF NOT EXISTS ext_shm_healing_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid      TEXT NOT NULL,
    healing_target    TEXT NOT NULL,
    target_type       TEXT NOT NULL,
    progress_before   REAL NOT NULL DEFAULT 0.0,
    progress_after    REAL NOT NULL DEFAULT 0.0,
    tick              INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_shm_heal_persona ON ext_shm_healing_log(persona_uuid);
"""

_AI_DECISIONS_TABLE = """
CREATE TABLE IF NOT EXISTS ext_shm_ai_decisions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid      TEXT NOT NULL,
    decision_type     TEXT NOT NULL,
    context           TEXT,
    reasoning         TEXT,
    outcome           TEXT,
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_shm_ai_persona ON ext_shm_ai_decisions(persona_uuid);
CREATE INDEX IF NOT EXISTS idx_shm_ai_type ON ext_shm_ai_decisions(decision_type);
"""

_BLOOD_REGENERATION_TABLE = """
CREATE TABLE IF NOT EXISTS ext_shm_blood_regeneration (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_uuid      TEXT NOT NULL,
    iron_stores       REAL NOT NULL DEFAULT 50.0,
    b12_stores        REAL NOT NULL DEFAULT 50.0,
    hydration_for_blood REAL NOT NULL DEFAULT 80.0,
    regeneration_rate REAL NOT NULL DEFAULT 1.0,
    UNIQUE(persona_uuid)
);
CREATE INDEX IF NOT EXISTS idx_shm_bloodreg_persona ON ext_shm_blood_regeneration(persona_uuid);
"""

# ══════════════════════════════════════════════════════════════════════
# Bone definitions (complete human skeleton - 206 bones)
# ══════════════════════════════════════════════════════════════════════

ALL_BONES = [
    # Axial Skeleton - Skull (22 bones)
    # Cranial (8)
    ("frontal_bone", "cranial", "head"),
    ("parietal_bone_left", "cranial", "head"),
    ("parietal_bone_right", "cranial", "head"),
    ("temporal_bone_left", "cranial", "head"),
    ("temporal_bone_right", "cranial", "head"),
    ("occipital_bone", "cranial", "head"),
    ("sphenoid_bone", "cranial", "head"),
    ("ethmoid_bone", "cranial", "head"),
    # Facial (14)
    ("nasal_bone_left", "facial", "face"),
    ("nasal_bone_right", "facial", "face"),
    ("maxilla_left", "facial", "face"),
    ("maxilla_right", "facial", "face"),
    ("zygomatic_bone_left", "facial", "face"),
    ("zygomatic_bone_right", "facial", "face"),
    ("lacrimal_bone_left", "facial", "face"),
    ("lacrimal_bone_right", "facial", "face"),
    ("palatine_bone_left", "facial", "face"),
    ("palatine_bone_right", "facial", "face"),
    ("inferior_nasal_concha_left", "facial", "face"),
    ("inferior_nasal_concha_right", "facial", "face"),
    ("vomer_bone", "facial", "face"),
    ("mandible", "facial", "face"),
    # Hyoid & Ossicles
    ("hyoid_bone", "neck", "neck"),
    ("malleus_left", "auditory", "head"),
    ("incus_left", "auditory", "head"),
    ("stapes_left", "auditory", "head"),
    ("malleus_right", "auditory", "head"),
    ("incus_right", "auditory", "head"),
    ("stapes_right", "auditory", "head"),

    # Axial Skeleton - Vertebral Column (26)
    ("cervical_vertebra_1_atlas", "vertebral", "neck"),
    ("cervical_vertebra_2_axis", "vertebral", "neck"),
    ("cervical_vertebra_3", "vertebral", "neck"),
    ("cervical_vertebra_4", "vertebral", "neck"),
    ("cervical_vertebra_5", "vertebral", "neck"),
    ("cervical_vertebra_6", "vertebral", "neck"),
    ("cervical_vertebra_7", "vertebral", "neck"),
    ("thoracic_vertebra_1", "vertebral", "upper_back"),
    ("thoracic_vertebra_2", "vertebral", "upper_back"),
    ("thoracic_vertebra_3", "vertebral", "upper_back"),
    ("thoracic_vertebra_4", "vertebral", "upper_back"),
    ("thoracic_vertebra_5", "vertebral", "upper_back"),
    ("thoracic_vertebra_6", "vertebral", "upper_back"),
    ("thoracic_vertebra_7", "vertebral", "upper_back"),
    ("thoracic_vertebra_8", "vertebral", "upper_back"),
    ("thoracic_vertebra_9", "vertebral", "upper_back"),
    ("thoracic_vertebra_10", "vertebral", "upper_back"),
    ("thoracic_vertebra_11", "vertebral", "upper_back"),
    ("thoracic_vertebra_12", "vertebral", "upper_back"),
    ("lumbar_vertebra_1", "vertebral", "lower_back"),
    ("lumbar_vertebra_2", "vertebral", "lower_back"),
    ("lumbar_vertebra_3", "vertebral", "lower_back"),
    ("lumbar_vertebra_4", "vertebral", "lower_back"),
    ("lumbar_vertebra_5", "vertebral", "lower_back"),
    ("sacrum", "vertebral", "pelvis"),
    ("coccyx", "vertebral", "pelvis"),

    # Rib Cage (25)
    ("sternum", "ribcage", "chest"),
    ("rib_1_left", "ribcage", "chest"),
    ("rib_1_right", "ribcage", "chest"),
    ("rib_2_left", "ribcage", "chest"),
    ("rib_2_right", "ribcage", "chest"),
    ("rib_3_left", "ribcage", "chest"),
    ("rib_3_right", "ribcage", "chest"),
    ("rib_4_left", "ribcage", "chest"),
    ("rib_4_right", "ribcage", "chest"),
    ("rib_5_left", "ribcage", "chest"),
    ("rib_5_right", "ribcage", "chest"),
    ("rib_6_left", "ribcage", "chest"),
    ("rib_6_right", "ribcage", "chest"),
    ("rib_7_left", "ribcage", "chest"),
    ("rib_7_right", "ribcage", "chest"),
    ("rib_8_left", "ribcage", "chest"),
    ("rib_8_right", "ribcage", "chest"),
    ("rib_9_left", "ribcage", "chest"),
    ("rib_9_right", "ribcage", "chest"),
    ("rib_10_left", "ribcage", "chest"),
    ("rib_10_right", "ribcage", "chest"),
    ("rib_11_left", "ribcage", "chest"),
    ("rib_11_right", "ribcage", "chest"),
    ("rib_12_left", "ribcage", "chest"),
    ("rib_12_right", "ribcage", "chest"),

    # Appendicular - Upper Limbs (64)
    # Shoulder girdle (4)
    ("clavicle_left", "shoulder", "shoulder"),
    ("clavicle_right", "shoulder", "shoulder"),
    ("scapula_left", "shoulder", "shoulder"),
    ("scapula_right", "shoulder", "shoulder"),
    # Arms (6)
    ("humerus_left", "arm", "upper_arm"),
    ("humerus_right", "arm", "upper_arm"),
    ("radius_left", "arm", "forearm"),
    ("radius_right", "arm", "forearm"),
    ("ulna_left", "arm", "forearm"),
    ("ulna_right", "arm", "forearm"),
    # Hands (54)
    ("carpal_scaphoid_left", "hand", "wrist"),
    ("carpal_lunate_left", "hand", "wrist"),
    ("carpal_triquetrum_left", "hand", "wrist"),
    ("carpal_pisiform_left", "hand", "wrist"),
    ("carpal_trapezium_left", "hand", "wrist"),
    ("carpal_trapezoid_left", "hand", "wrist"),
    ("carpal_capitate_left", "hand", "wrist"),
    ("carpal_hamate_left", "hand", "wrist"),
    ("carpal_scaphoid_right", "hand", "wrist"),
    ("carpal_lunate_right", "hand", "wrist"),
    ("carpal_triquetrum_right", "hand", "wrist"),
    ("carpal_pisiform_right", "hand", "wrist"),
    ("carpal_trapezium_right", "hand", "wrist"),
    ("carpal_trapezoid_right", "hand", "wrist"),
    ("carpal_capitate_right", "hand", "wrist"),
    ("carpal_hamate_right", "hand", "wrist"),
    ("metacarpal_1_left", "hand", "palm"),
    ("metacarpal_2_left", "hand", "palm"),
    ("metacarpal_3_left", "hand", "palm"),
    ("metacarpal_4_left", "hand", "palm"),
    ("metacarpal_5_left", "hand", "palm"),
    ("metacarpal_1_right", "hand", "palm"),
    ("metacarpal_2_right", "hand", "palm"),
    ("metacarpal_3_right", "hand", "palm"),
    ("metacarpal_4_right", "hand", "palm"),
    ("metacarpal_5_right", "hand", "palm"),
    ("proximal_phalanx_1_left", "hand", "finger"),
    ("proximal_phalanx_2_left", "hand", "finger"),
    ("proximal_phalanx_3_left", "hand", "finger"),
    ("proximal_phalanx_4_left", "hand", "finger"),
    ("proximal_phalanx_5_left", "hand", "finger"),
    ("proximal_phalanx_1_right", "hand", "finger"),
    ("proximal_phalanx_2_right", "hand", "finger"),
    ("proximal_phalanx_3_right", "hand", "finger"),
    ("proximal_phalanx_4_right", "hand", "finger"),
    ("proximal_phalanx_5_right", "hand", "finger"),
    ("middle_phalanx_2_left", "hand", "finger"),
    ("middle_phalanx_3_left", "hand", "finger"),
    ("middle_phalanx_4_left", "hand", "finger"),
    ("middle_phalanx_5_left", "hand", "finger"),
    ("middle_phalanx_2_right", "hand", "finger"),
    ("middle_phalanx_3_right", "hand", "finger"),
    ("middle_phalanx_4_right", "hand", "finger"),
    ("middle_phalanx_5_right", "hand", "finger"),
    ("distal_phalanx_1_left", "hand", "finger"),
    ("distal_phalanx_2_left", "hand", "finger"),
    ("distal_phalanx_3_left", "hand", "finger"),
    ("distal_phalanx_4_left", "hand", "finger"),
    ("distal_phalanx_5_left", "hand", "finger"),
    ("distal_phalanx_1_right", "hand", "finger"),
    ("distal_phalanx_2_right", "hand", "finger"),
    ("distal_phalanx_3_right", "hand", "finger"),
    ("distal_phalanx_4_right", "hand", "finger"),
    ("distal_phalanx_5_right", "hand", "finger"),

    # Appendicular - Lower Limbs (62)
    # Pelvic girdle (2)
    ("hip_bone_left", "pelvis", "hip"),
    ("hip_bone_right", "pelvis", "hip"),
    # Legs (8)
    ("femur_left", "leg", "thigh"),
    ("femur_right", "leg", "thigh"),
    ("patella_left", "leg", "knee"),
    ("patella_right", "leg", "knee"),
    ("tibia_left", "leg", "shin"),
    ("tibia_right", "leg", "shin"),
    ("fibula_left", "leg", "shin"),
    ("fibula_right", "leg", "shin"),
    # Feet (52)
    ("calcaneus_left", "foot", "heel"),
    ("calcaneus_right", "foot", "heel"),
    ("talus_left", "foot", "ankle"),
    ("talus_right", "foot", "ankle"),
    ("navicular_left", "foot", "midfoot"),
    ("navicular_right", "foot", "midfoot"),
    ("cuboid_left", "foot", "midfoot"),
    ("cuboid_right", "foot", "midfoot"),
    ("medial_cuneiform_left", "foot", "midfoot"),
    ("medial_cuneiform_right", "foot", "midfoot"),
    ("intermediate_cuneiform_left", "foot", "midfoot"),
    ("intermediate_cuneiform_right", "foot", "midfoot"),
    ("lateral_cuneiform_left", "foot", "midfoot"),
    ("lateral_cuneiform_right", "foot", "midfoot"),
    ("metatarsal_1_left", "foot", "forefoot"),
    ("metatarsal_2_left", "foot", "forefoot"),
    ("metatarsal_3_left", "foot", "forefoot"),
    ("metatarsal_4_left", "foot", "forefoot"),
    ("metatarsal_5_left", "foot", "forefoot"),
    ("metatarsal_1_right", "foot", "forefoot"),
    ("metatarsal_2_right", "foot", "forefoot"),
    ("metatarsal_3_right", "foot", "forefoot"),
    ("metatarsal_4_right", "foot", "forefoot"),
    ("metatarsal_5_right", "foot", "forefoot"),
    ("proximal_toe_phalanx_1_left", "foot", "toe"),
    ("proximal_toe_phalanx_2_left", "foot", "toe"),
    ("proximal_toe_phalanx_3_left", "foot", "toe"),
    ("proximal_toe_phalanx_4_left", "foot", "toe"),
    ("proximal_toe_phalanx_5_left", "foot", "toe"),
    ("proximal_toe_phalanx_1_right", "foot", "toe"),
    ("proximal_toe_phalanx_2_right", "foot", "toe"),
    ("proximal_toe_phalanx_3_right", "foot", "toe"),
    ("proximal_toe_phalanx_4_right", "foot", "toe"),
    ("proximal_toe_phalanx_5_right", "foot", "toe"),
    ("middle_toe_phalanx_2_left", "foot", "toe"),
    ("middle_toe_phalanx_3_left", "foot", "toe"),
    ("middle_toe_phalanx_4_left", "foot", "toe"),
    ("middle_toe_phalanx_5_left", "foot", "toe"),
    ("middle_toe_phalanx_2_right", "foot", "toe"),
    ("middle_toe_phalanx_3_right", "foot", "toe"),
    ("middle_toe_phalanx_4_right", "foot", "toe"),
    ("middle_toe_phalanx_5_right", "foot", "toe"),
    ("distal_toe_phalanx_1_left", "foot", "toe"),
    ("distal_toe_phalanx_2_left", "foot", "toe"),
    ("distal_toe_phalanx_3_left", "foot", "toe"),
    ("distal_toe_phalanx_4_left", "foot", "toe"),
    ("distal_toe_phalanx_5_left", "foot", "toe"),
    ("distal_toe_phalanx_1_right", "foot", "toe"),
    ("distal_toe_phalanx_2_right", "foot", "toe"),
    ("distal_toe_phalanx_3_right", "foot", "toe"),
    ("distal_toe_phalanx_4_right", "foot", "toe"),
    ("distal_toe_phalanx_5_right", "foot", "toe"),
]

# ══════════════════════════════════════════════════════════════════════
# Organ definitions
# ══════════════════════════════════════════════════════════════════════

ALL_ORGANS = [
    "brain", "heart", "lung_left", "lung_right",
    "liver", "kidney_left", "kidney_right",
    "stomach", "intestines", "spleen",
    "pancreas", "bladder", "gallbladder",
]

# ══════════════════════════════════════════════════════════════════════
# Muscle group definitions
# ══════════════════════════════════════════════════════════════════════

ALL_MUSCLES = [
    "chest", "back", "shoulders", "biceps",
    "triceps", "forearms", "quadriceps", "hamstrings",
    "glutes", "calves", "abdominals", "neck",
]

# ══════════════════════════════════════════════════════════════════════
# Blood type assignment
# ══════════════════════════════════════════════════════════════════════

BLOOD_TYPES = ["O+", "A+", "B+", "AB+", "O-", "A-", "B-", "AB-"]
BLOOD_TYPE_WEIGHTS = [0.37, 0.28, 0.18, 0.06, 0.05, 0.04, 0.01, 0.01]


# ══════════════════════════════════════════════════════════════════════
# Schema initialization
# ══════════════════════════════════════════════════════════════════════

def ensure_schema():
    """Create all SHM tables if they don't exist. Safe to call repeatedly."""
    db = get_db()
    schemas = [
        _BLOOD_TABLE, _BONES_TABLE, _ORGANS_TABLE, _MUSCLES_TABLE,
        _GENETICS_TABLE, _DISEASES_TABLE, _DISEASE_SPREAD_TABLE,
        _HYGIENE_TABLE, _IMMUNE_TABLE, _PAIN_TABLE,
        _COMBAT_SKILLS_TABLE, _NEGATIVE_TRAITS_TABLE,
        _HEALING_LOG_TABLE, _AI_DECISIONS_TABLE, _BLOOD_REGENERATION_TABLE,
    ]
    for table_sql in schemas:
        for stmt in table_sql.strip().split(";"):
            s = stmt.strip()
            if s and s.upper().startswith("CREATE"):
                try:
                    db.execute(s)
                except Exception as e:
                    log.warn("shm_database", f"Schema stmt failed: {e}")
    log.info("shm_database", "SHM schema initialized")


def initialize_persona(persona_uuid: str, archetype: str):
    """Initialize all SHM database records for a new persona.

    Creates: blood, bones (206), organs, muscles, genetics, hygiene,
    combat skills, blood regeneration.
    """
    try:
        import random

    except Exception as e:
        log.error(f"initialize_persona failed: {e}")
        return None
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()

    # ── Blood ──────────────────────────────────────────────────────
    blood_type = random.choices(BLOOD_TYPES, BLOOD_TYPE_WEIGHTS)[0]
    blood_vol = random.randint(4500, 5500)
    db.execute("""
        INSERT OR IGNORE INTO ext_shm_blood
        (persona_uuid, blood_volume_ml, max_blood_volume, blood_type)
        VALUES (?, ?, ?, ?)
    """, (persona_uuid, blood_vol, blood_vol, blood_type))

    # ── Blood Regeneration ─────────────────────────────────────────
    db.execute("""
        INSERT OR IGNORE INTO ext_shm_blood_regeneration
        (persona_uuid, iron_stores, b12_stores, hydration_for_blood, regeneration_rate)
        VALUES (?, ?, ?, ?, ?)
    """, (persona_uuid, random.uniform(30, 70), random.uniform(30, 70),
          random.uniform(60, 90), random.uniform(0.8, 1.2)))

    # ── Bones (206) ─────────────────────────────────────────────────
    for bone_name, bone_group, location in ALL_BONES:
        db.execute("""
            INSERT OR IGNORE INTO ext_shm_anatomy_bones
            (persona_uuid, bone_name, bone_group, location, fractured, healing_progress, pain_contribution)
            VALUES (?, ?, ?, ?, 0, 0.0, 0.0)
        """, (persona_uuid, bone_name, bone_group, location))

    # ── Organs ──────────────────────────────────────────────────────
    for organ in ALL_ORGANS:
        base_health = random.uniform(85, 100)
        db.execute("""
            INSERT OR IGNORE INTO ext_shm_anatomy_organs
            (persona_uuid, organ_name, health)
            VALUES (?, ?, ?)
        """, (persona_uuid, organ, base_health))

    # ── Muscles ─────────────────────────────────────────────────────
    for muscle in ALL_MUSCLES:
        base_strength = random.uniform(25, 50)
        if archetype in ("warrior", "miner", "adventurer"):
            base_strength = random.uniform(40, 65)
        elif archetype in ("mage", "merchant"):
            base_strength = random.uniform(15, 35)
        db.execute("""
            INSERT OR IGNORE INTO ext_shm_muscles
            (persona_uuid, muscle_group, strength, max_strength, protein_level, fatigue)
            VALUES (?, ?, ?, ?, ?, 0)
        """, (persona_uuid, muscle, base_strength, 100.0, random.uniform(60, 90)))

    # ── Genetics ────────────────────────────────────────────────────
    _create_genetics(db, persona_uuid, archetype)

    # ── Hygiene ─────────────────────────────────────────────────────
    hygiene_base = random.uniform(50, 80)
    oral_base = random.uniform(40, 75)
    if archetype in ("merchant", "mage", "warrior"):
        hygiene_base = random.uniform(60, 90)
        oral_base = random.uniform(55, 85)
    elif archetype in ("vagabond", "miner"):
        hygiene_base = random.uniform(30, 55)
        oral_base = random.uniform(25, 50)
    db.execute("""
        INSERT OR IGNORE INTO ext_shm_hygiene
        (persona_uuid, personal_cleanliness, oral_hygiene, clothing_cleanliness,
         wound_care, environment_cleanliness)
        VALUES (?, ?, ?, ?, 100.0, ?)
    """, (persona_uuid, hygiene_base, oral_base, hygiene_base * 0.9,
          hygiene_base * 0.8))

    # ── Combat Skills ───────────────────────────────────────────────
    skill_base = 1.0
    if archetype == "warrior":
        skill_base = 15.0
    elif archetype in ("adventurer", "miner", "vagabond"):
        skill_base = 8.0
    elif archetype == "mage":
        skill_base = 3.0

    COMBAT_SKILL_NAMES = [
        "blades", "blunt", "archery", "polearms", "unarmed",
        "blocking", "dodging", "critical_strike", "combat_awareness", "first_aid",
    ]
    for skill in COMBAT_SKILL_NAMES:
        level = skill_base + random.uniform(-3, 5)
        level = max(1.0, min(30.0, level))

        if skill == "first_aid" and archetype == "mage":
            level += 5
        if skill in ("blades", "blunt") and archetype == "warrior":
            level += 8

        db.execute("""
            INSERT OR IGNORE INTO ext_shm_combat_skills
            (persona_uuid, skill_name, level, proficiency_title, xp_current, xp_to_next)
            VALUES (?, ?, ?, ?, 0.0, ?)
        """, (persona_uuid, skill, level,
              _get_proficiency_title(level),
              10.0 + (level * 1.5)))

    log.info("shm_database",
             f"Initialized SHM data for persona {persona_uuid[:8]} ({archetype})")


def _create_genetics(db, persona_uuid: str, archetype: str):
    """Generate genetic traits from archetype and personality."""
    import random
    from AUCTIONHOUSE.EXTENSIONS.SIMULATED_PEOPLE.sp_database import (
        get_persona_by_uuid
    )

    persona = get_persona_by_uuid(persona_uuid)
    traits = {}
    if persona and persona.get("personality_traits"):
        import json
        try:
            traits = json.loads(persona["personality_traits"])
        except (json.JSONDecodeError, TypeError):
            pass

    # Base values by archetype
    archetype_genetics = {
        "adventurer": {"immune_potency": 1.1, "pain_tolerance": 1.2,
                       "healing_factor": 1.0, "metabolic_rate": 1.3,
                       "disease_resistance": 0.9, "blood_efficiency": 1.2},
        "merchant": {"immune_potency": 0.9, "pain_tolerance": 0.8,
                     "healing_factor": 1.1, "metabolic_rate": 0.8,
                     "disease_resistance": 1.1, "toxin_resistance": 1.3},
        "builder": {"immune_potency": 1.0, "pain_tolerance": 1.0,
                    "healing_factor": 1.1, "metabolic_rate": 0.9,
                    "organ_vitality": 1.2, "nerve_density": 0.9},
        "miner": {"immune_potency": 1.2, "pain_tolerance": 1.3,
                  "healing_factor": 1.0, "metabolic_rate": 1.2,
                  "muscle_growth_rate": 1.2, "toxin_resistance": 1.2},
        "farmer": {"immune_potency": 1.3, "pain_tolerance": 1.0,
                   "healing_factor": 1.1, "disease_resistance": 1.3,
                   "organ_vitality": 1.1, "metabolic_rate": 1.1},
        "warrior": {"immune_potency": 1.0, "pain_tolerance": 1.4,
                    "healing_factor": 1.0, "muscle_growth_rate": 1.4,
                    "blood_efficiency": 1.3, "nerve_density": 0.7},
        "mage": {"immune_potency": 1.1, "pain_tolerance": 0.7,
                 "healing_factor": 0.9, "toxin_resistance": 0.8,
                 "nerve_density": 1.3, "organ_vitality": 0.9},
        "vagabond": {"immune_potency": 0.8, "pain_tolerance": 0.9,
                     "healing_factor": 0.8, "disease_resistance": 0.7,
                     "metabolic_rate": 1.4, "toxin_resistance": 1.0},
    }

    base = archetype_genetics.get(archetype, archetype_genetics["adventurer"])

    # Modify by personality traits
    trait_modifiers = {
        "endurance": ["metabolic_rate", "muscle_growth_rate"],
        "wisdom": ["immune_potency", "toxin_resistance"],
        "bravery": ["pain_tolerance"],
        "thrift": ["healing_factor", "organ_vitality"],
        "patience": ["disease_resistance"],
        "courage": ["blood_efficiency"],
        "aggression": ["muscle_growth_rate"],
        "simplicity": ["organ_vitality"],
    }

    for trait_name, target_genes in trait_modifiers.items():
        trait_val = traits.get(trait_name, 5)
        modifier = (trait_val - 5) * 0.05  # ±0.2 range
        for gene in target_genes:
            if gene in base:
                base[gene] += modifier

    # Add random variation
    for key in ["metabolic_rate", "immune_potency", "pain_tolerance",
                "healing_factor", "disease_resistance", "muscle_growth_rate",
                "toxin_resistance", "blood_efficiency", "organ_vitality",
                "nerve_density"]:
        base[key] = base.get(key, 1.0) + random.uniform(-0.1, 0.1)
        base[key] = max(0.3, min(2.0, base[key]))

    db.execute("""
        INSERT OR REPLACE INTO ext_shm_genetics
        (persona_uuid, metabolic_rate, immune_potency, pain_tolerance,
         healing_factor, disease_resistance, muscle_growth_rate,
         toxin_resistance, blood_efficiency, organ_vitality, nerve_density)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (persona_uuid,
          round(base.get("metabolic_rate", 1.0), 3),
          round(base.get("immune_potency", 1.0), 3),
          round(base.get("pain_tolerance", 1.0), 3),
          round(base.get("healing_factor", 1.0), 3),
          round(base.get("disease_resistance", 1.0), 3),
          round(base.get("muscle_growth_rate", 1.0), 3),
          round(base.get("toxin_resistance", 1.0), 3),
          round(base.get("blood_efficiency", 1.0), 3),
          round(base.get("organ_vitality", 1.0), 3),
          round(base.get("nerve_density", 1.0), 3)))


def _get_proficiency_title(level: float) -> str:
    if level < 1:
        return "Unskilled"
    elif level <= 10:
        return "Novice"
    elif level <= 25:
        return "Apprentice"
    elif level <= 40:
        return "Journeyman"
    elif level <= 55:
        return "Expert"
    elif level <= 70:
        return "Master"
    elif level <= 85:
        return "Grandmaster"
    else:
        return "Legendary"


def cleanup_persona(persona_uuid: str):
    """Remove all SHM data for a persona (when they die)."""
    db = get_db()
    tables = [
        "ext_shm_blood", "ext_shm_blood_regeneration",
        "ext_shm_anatomy_bones", "ext_shm_anatomy_organs",
        "ext_shm_muscles", "ext_shm_genetics",
        "ext_shm_diseases", "ext_shm_disease_spread",
        "ext_shm_hygiene", "ext_shm_immune_response",
        "ext_shm_pain", "ext_shm_combat_skills",
        "ext_shm_negative_traits", "ext_shm_healing_log",
        "ext_shm_ai_decisions",
    ]
    for table in tables:
        try:
            db.execute(f"DELETE FROM {table} WHERE persona_uuid = ?", (persona_uuid,))
        except Exception:
            pass
    log.info("shm_database", f"Cleaned up SHM data for dead persona {persona_uuid[:8]}")

