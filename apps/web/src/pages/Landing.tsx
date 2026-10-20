import BindingThesis from "../components/landing/BindingThesis";
import FeatureGrid from "../components/landing/FeatureGrid";
import FinalCta from "../components/landing/FinalCta";
import Hero from "../components/landing/Hero";
import HowItWorks from "../components/landing/HowItWorks";
import LandingFooter from "../components/landing/LandingFooter";
import LandingNav from "../components/landing/LandingNav";
import ValidationShowcase from "../components/landing/ValidationShowcase";

export default function Landing() {
  return (
    <div className="min-h-[100dvh] bg-bg text-text">
      <LandingNav />
      <Hero />
      <BindingThesis />
      <HowItWorks />
      <FeatureGrid />
      <ValidationShowcase />
      <FinalCta />
      <LandingFooter />
    </div>
  );
}
