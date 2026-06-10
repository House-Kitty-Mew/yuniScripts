# Test-DatagramCompatibility.Tests.ps1
# Pester unit tests for Test-DatagramCompatibility.ps1

BeforeAll {
    # Source the module under test
    $script:modulePath = Join-Path $PSScriptRoot '..\..\FUNCTIONS\Discovery\Test-DatagramCompatibility.ps1'
    . $script:modulePath
}

Describe 'Test-VersionCompatibility' -Tag 'UnitTest' {

    Context 'Standard version strings (no whitespace)' {

        It 'should return true when available version equals required version' {
            $result = Test-VersionCompatibility -Required '1.0.0' -Available '1.0.0'
            $result | Should -Be $true
        }

        It 'should return true when available version is higher than required' {
            $result = Test-VersionCompatibility -Required '1.0.0' -Available '1.2.3'
            $result | Should -Be $true
        }

        It 'should return false when available version is lower than required' {
            $result = Test-VersionCompatibility -Required '2.0.0' -Available '1.9.9'
            $result | Should -Be $false
        }

        It 'should return true when available is greater in minor version (same major)' {
            $result = Test-VersionCompatibility -Required '2.1.0' -Available '2.5.0'
            $result | Should -Be $true
        }

        It 'should return false when available is lower in major version' {
            $result = Test-VersionCompatibility -Required '3.0.0' -Available '2.0.0'
            $result | Should -Be $false
        }

        It 'should handle two-part version strings (major.minor)' {
            $result = Test-VersionCompatibility -Required '1.5' -Available '1.8'
            $result | Should -Be $true
        }

        It 'should return false when two-part version is lower' {
            $result = Test-VersionCompatibility -Required '1.8' -Available '1.5'
            $result | Should -Be $false
        }

        It 'should handle single-part version strings' {
            $result = Test-VersionCompatibility -Required '5' -Available '6'
            $result | Should -Be $true
        }

        It 'should return false when single-part version is lower' {
            $result = Test-VersionCompatibility -Required '6' -Available '5'
            $result | Should -Be $false
        }
    }

    Context 'Whitespace-padded version strings (the original bug)' {

        It 'should handle leading whitespace in Required version' {
            $result = Test-VersionCompatibility -Required '  1.0.0' -Available '1.0.0'
            $result | Should -Be $true
        }

        It 'should handle trailing whitespace in Required version' {
            $result = Test-VersionCompatibility -Required '1.0.0  ' -Available '1.2.0'
            $result | Should -Be $true
        }

        It 'should handle leading whitespace in Available version' {
            $result = Test-VersionCompatibility -Required '1.0.0' -Available '  1.2.0'
            $result | Should -Be $true
        }

        It 'should handle trailing whitespace in Available version' {
            $result = Test-VersionCompatibility -Required '1.0.0' -Available '1.2.0  '
            $result | Should -Be $true
        }

        It 'should handle whitespace on both Required and Available' {
            $result = Test-VersionCompatibility -Required '  2.0.0  ' -Available '  2.5.0  '
            $result | Should -Be $true
        }

        It 'should correctly reject with whitespace when available is lower' {
            $result = Test-VersionCompatibility -Required '  3.0.0  ' -Available '  2.0.0  '
            $result | Should -Be $false
        }

        It 'should handle tabs and mixed whitespace' {
            $result = Test-VersionCompatibility -Required "`t1.0.0" -Available "1.5.0`t"
            $result | Should -Be $true
        }

        It 'should handle only-whitespace then trimmed value for equal comparison' {
            $result = Test-VersionCompatibility -Required '  ' -Available '  '
            # Both trim to empty string, split gives @(), then for loop doesn't execute
            # and returns $true (all parts equal)
            $result | Should -Be $true
        }
    }

    Context 'Edge cases and error handling' {

        It 'should handle empty strings' {
            $result = Test-VersionCompatibility -Required '' -Available ''
            $result | Should -Be $true
        }

        It 'should handle strings with only whitespace (both empty after trim)' {
            # Both empty after trim => no parts => returns $true (equal)
            $result = Test-VersionCompatibility -Required '   ' -Available '     '
            $result | Should -Be $true
        }

        It 'should handle version strings with different part counts' {
            # 3-part vs 2-part: 2.0.0 vs 2.0 => the missing part defaults to 0
            $result = Test-VersionCompatibility -Required '2.0.0' -Available '2.0'
            $result | Should -Be $false  # 2.0 = 2.0.0 with third part defaulting to 0
        }

        It 'should handle version strings where available has more parts' {
            $result = Test-VersionCompatibility -Required '2.0' -Available '2.0.1'
            $result | Should -Be $true
        }

        It 'should handle version strings with leading zeros in parts' {
            $result = Test-VersionCompatibility -Required '01.02.03' -Available '01.02.04'
            $result | Should -Be $true
        }

        It 'should throw when version parts contain non-numeric content' {
            # When Trim() is applied but the content is non-numeric, [int] cast returns $null
            # which -as [int] preserves as $null, then comparing $null to an int may fail
            { Test-VersionCompatibility -Required '1.a.0' -Available '1.0.0' } | Should -Throw
        }
    }
}

Describe 'Test-DatagramCompatibility' -Tag 'UnitTest' {

    Context 'Loader capability matching' {

        It 'should return true when datagram has no version requirements' {
            $datagram = [PSCustomObject]@{ FunctionVersions = @{} }
            $result = Test-DatagramCompatibility -Datagram $datagram
            $result | Should -Be $true
        }

        It 'should return true when all required capabilities are met' {
            $datagram = [PSCustomObject]@{
                FunctionVersions = @{
                    'Loader' = '1.0'
                    'Database' = '1.0'
                }
            }
            $loader = @{
                'Loader' = '1.0'
                'Database' = '1.0'
                'Buttons' = '1.0'
            }
            $result = Test-DatagramCompatibility -Datagram $datagram -LoaderCapabilities $loader
            $result | Should -Be $true
        }

        It 'should return false when a required capability is missing' {
            $datagram = [PSCustomObject]@{
                FunctionVersions = @{
                    'UnknownFeature' = '1.0'
                }
            }
            $loader = @{
                'Loader' = '1.0'
            }
            $result = Test-DatagramCompatibility -Datagram $datagram -LoaderCapabilities $loader
            $result | Should -Be $false
        }

        It 'should return false when version requirements are not met' {
            $datagram = [PSCustomObject]@{
                FunctionVersions = @{
                    'Loader' = '3.0'
                }
            }
            $loader = @{
                'Loader' = '2.0'
            }
            $result = Test-DatagramCompatibility -Datagram $datagram -LoaderCapabilities $loader
            $result | Should -Be $false
        }
    }
}
