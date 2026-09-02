"""Computer vision for the card scanner.

Detection, rectification and perceptual hashing all live on the server (ADR-024). The
phone is a camera: it captures frames and sends them. Putting the vision here is what
lets it use real OpenCV, and -- just as important -- what makes it testable against a
corpus of images in CI rather than only by walking to a phone.
"""
