"""Channel group and preset tables mirrored from QLC+ (engine/src/qlcchannel.*)."""

from __future__ import annotations

from enum import IntEnum


class ChannelGroup(IntEnum):
    """QLCChannel::Group. The numbers are what getChannelsValues reports."""

    INTENSITY = 0
    COLOUR = 1
    GOBO = 2
    SPEED = 3
    PAN = 4
    TILT = 5
    SHUTTER = 6
    PRISM = 7
    BEAM = 8
    EFFECT = 9
    MAINTENANCE = 10
    NOTHING = 11

    @classmethod
    def from_xml(cls, name: str) -> ChannelGroup:
        try:
            return GROUP_NAMES[name]
        except KeyError:
            return cls.NOTHING


GROUP_NAMES: dict[str, ChannelGroup] = {
    "Intensity": ChannelGroup.INTENSITY,
    "Colour": ChannelGroup.COLOUR,
    "Gobo": ChannelGroup.GOBO,
    "Speed": ChannelGroup.SPEED,
    "Pan": ChannelGroup.PAN,
    "Tilt": ChannelGroup.TILT,
    "Shutter": ChannelGroup.SHUTTER,
    "Prism": ChannelGroup.PRISM,
    "Beam": ChannelGroup.BEAM,
    "Effect": ChannelGroup.EFFECT,
    "Maintenance": ChannelGroup.MAINTENANCE,
    "Nothing": ChannelGroup.NOTHING,
}

# QLCChannel::PrimaryColour
PRIMARY_COLOURS: dict[str, int] = {
    "Red": 0xFF0000,
    "Green": 0x00FF00,
    "Blue": 0x0000FF,
    "Cyan": 0x00FFFF,
    "Magenta": 0xFF00FF,
    "Yellow": 0xFFFF00,
    "Amber": 0xFF7E00,
    "White": 0xFFFFFF,
    "UV": 0x9400D3,
    "Lime": 0xADFF2F,
    "Indigo": 0x4B0082,
}

_I = ChannelGroup.INTENSITY

# Channel preset -> (group, primary colour name, is LSB/fine), generated from
# QLCChannel::setPreset() at QLC+ 4.14.4.
CHANNEL_PRESETS: dict[str, tuple[ChannelGroup, str | None, bool]] = {
    "IntensityMasterDimmer": (_I, None, False),
    "IntensityMasterDimmerFine": (_I, None, True),
    "IntensityDimmer": (_I, None, False),
    "IntensityDimmerFine": (_I, None, True),
    "IntensityRed": (_I, "Red", False),
    "IntensityRedFine": (_I, "Red", True),
    "IntensityGreen": (_I, "Green", False),
    "IntensityGreenFine": (_I, "Green", True),
    "IntensityBlue": (_I, "Blue", False),
    "IntensityBlueFine": (_I, "Blue", True),
    "IntensityCyan": (_I, "Cyan", False),
    "IntensityCyanFine": (_I, "Cyan", True),
    "IntensityMagenta": (_I, "Magenta", False),
    "IntensityMagentaFine": (_I, "Magenta", True),
    "IntensityYellow": (_I, "Yellow", False),
    "IntensityYellowFine": (_I, "Yellow", True),
    "IntensityAmber": (_I, "Amber", False),
    "IntensityAmberFine": (_I, "Amber", True),
    "IntensityWhite": (_I, "White", False),
    "IntensityWhiteFine": (_I, "White", True),
    "IntensityUV": (_I, "UV", False),
    "IntensityUVFine": (_I, "UV", True),
    "IntensityIndigo": (_I, "Indigo", False),
    "IntensityIndigoFine": (_I, "Indigo", True),
    "IntensityLime": (_I, "Lime", False),
    "IntensityLimeFine": (_I, "Lime", True),
    "IntensityHue": (_I, None, False),
    "IntensityHueFine": (_I, None, True),
    "IntensitySaturation": (_I, None, False),
    "IntensitySaturationFine": (_I, None, True),
    "IntensityLightness": (_I, None, False),
    "IntensityLightnessFine": (_I, None, True),
    "IntensityValue": (_I, None, False),
    "IntensityValueFine": (_I, None, True),
    "PositionPan": (ChannelGroup.PAN, None, False),
    "PositionPanFine": (ChannelGroup.PAN, None, True),
    "PositionTilt": (ChannelGroup.TILT, None, False),
    "PositionTiltFine": (ChannelGroup.TILT, None, True),
    "PositionXAxis": (ChannelGroup.PAN, None, False),
    "PositionYAxis": (ChannelGroup.TILT, None, False),
    "SpeedPanSlowFast": (ChannelGroup.SPEED, None, False),
    "SpeedPanFastSlow": (ChannelGroup.SPEED, None, False),
    "SpeedTiltSlowFast": (ChannelGroup.SPEED, None, False),
    "SpeedTiltFastSlow": (ChannelGroup.SPEED, None, False),
    "SpeedPanTiltSlowFast": (ChannelGroup.SPEED, None, False),
    "SpeedPanTiltFastSlow": (ChannelGroup.SPEED, None, False),
    "ColorMacro": (ChannelGroup.COLOUR, None, False),
    "ColorWheel": (ChannelGroup.COLOUR, None, False),
    "ColorWheelFine": (ChannelGroup.COLOUR, None, True),
    "ColorRGBMixer": (ChannelGroup.COLOUR, None, False),
    "ColorCTOMixer": (ChannelGroup.COLOUR, None, False),
    "ColorCTCMixer": (ChannelGroup.COLOUR, None, False),
    "ColorCTBMixer": (ChannelGroup.COLOUR, None, False),
    "GoboWheel": (ChannelGroup.GOBO, None, False),
    "GoboWheelFine": (ChannelGroup.GOBO, None, True),
    "GoboIndex": (ChannelGroup.GOBO, None, False),
    "GoboIndexFine": (ChannelGroup.GOBO, None, True),
    "ShutterStrobeSlowFast": (ChannelGroup.SHUTTER, None, False),
    "ShutterStrobeFastSlow": (ChannelGroup.SHUTTER, None, False),
    "ShutterIrisMinToMax": (ChannelGroup.SHUTTER, None, False),
    "ShutterIrisMaxToMin": (ChannelGroup.SHUTTER, None, False),
    "ShutterIrisFine": (ChannelGroup.SHUTTER, None, True),
    "BeamFocusNearFar": (ChannelGroup.BEAM, None, False),
    "BeamFocusFarNear": (ChannelGroup.BEAM, None, False),
    "BeamFocusFine": (ChannelGroup.BEAM, None, True),
    "BeamZoomSmallBig": (ChannelGroup.BEAM, None, False),
    "BeamZoomBigSmall": (ChannelGroup.BEAM, None, False),
    "BeamZoomFine": (ChannelGroup.BEAM, None, True),
    "PrismRotationFastSlow": (ChannelGroup.PRISM, None, False),
    "PrismRotationSlowFast": (ChannelGroup.PRISM, None, False),
    "NoFunction": (ChannelGroup.NOTHING, None, False),
}

# Presets whose channel is the main brightness of a head / fixture.
DIMMER_PRESETS = frozenset({"IntensityMasterDimmer", "IntensityDimmer"})
DIMMER_FINE_PRESETS = frozenset({"IntensityMasterDimmerFine", "IntensityDimmerFine"})
MASTER_DIMMER_PRESETS = frozenset({"IntensityMasterDimmer", "IntensityMasterDimmerFine"})

# Capability presets that describe a colour / gobo slot.
COLOUR_CAPABILITY_PRESETS = frozenset({"ColorMacro", "ColorDoubleMacro", "ColorWheelIndex"})
GOBO_CAPABILITY_PRESETS = frozenset({"GoboMacro", "GoboShakeMacro"})
